"""Local, checksummed NLI inference and a content-addressed SQLite score cache."""

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import replace
from importlib.metadata import version
from itertools import batched
from pathlib import Path

from .data import file_digest
from .schema import ScoredSpan, canonical_bytes, digest, integer, load_json, string

LOCK_DIGEST = "c8a14e6bb08e6528b9043c3085c8d00fe56bc64fbbd3504789baa8b50e6f0991"
LABELS = {0: "contradiction", 1: "entailment", 2: "neutral"}
MAX_TOKENS = 512


def check_model(model_dir, lock_path):
    lock = load_json(lock_path)
    if digest(lock) != LOCK_DIGEST:
        raise ValueError("unexpected model lock")
    directory = Path(model_dir)
    if {p.name for p in directory.iterdir()} != set(lock["files"]):
        raise ValueError("unexpected or missing model files")
    for name, pin in lock["files"].items():
        path = directory / name
        if (
            not path.is_file()
            or path.stat().st_size != pin["bytes"]
            or file_digest(path) != pin["sha256"]
        ):
            raise ValueError(f"model file checksum mismatch: {name}")
    return lock


def _millionths(probs):
    if len(probs) != 3 or not all(math.isfinite(p) and 0 <= p <= 1 for p in probs):
        raise ValueError("invalid NLI probabilities")
    total = sum(probs)
    if abs(total - 1) > 1e-5:
        raise ValueError("invalid softmax sum")
    scaled = [p * 1_000_000 / total for p in probs]
    quantized = [math.floor(v) for v in scaled]
    order = sorted(range(3), key=lambda i: (quantized[i] - scaled[i], i))
    for i in order[: 1_000_000 - sum(quantized)]:
        quantized[i] += 1
    return quantized


class NeuralVerifier:
    def __init__(self, corpus, model_dir, lock_path, device="cpu", batch_size=16):
        lock = check_model(model_dir, lock_path)
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.corpus, self.device, self.batch_size = (
            corpus,
            device,
            integer(batch_size, 1, 256),
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_dir,
            local_files_only=True,
            trust_remote_code=False,
            use_fast=True,
            truncation_side="right",
        )
        if not self.tokenizer.is_fast:
            raise ValueError("fast tokenizer with sequence offsets required")
        self.network = AutoModelForSequenceClassification.from_pretrained(
            model_dir,
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
            dtype=torch.float32,
        )
        if (
            self.network.config.id2label != LABELS
            or self.network.config.max_position_embeddings != MAX_TOKENS
        ):
            raise ValueError("unexpected model label mapping or context size")
        self.network.to(device).eval()
        self.metadata = {
            "lock": lock,
            "encoding": "pair-premise-first/right-only-first/offset-prefix-v1",
            "max_tokens": MAX_TOKENS,
            "labels": list(LABELS.values()),
            "quantization": "largest-remainder-millionths-v1",
            "dtype": "float32",
            "device": str(device),
            "batch_size": self.batch_size,
            "versions": {
                p: version(p)
                for p in ("torch", "transformers", "tokenizers", "safetensors")
            },
        }
        self.model_digest = digest(self.metadata)
        self.truncated_pairs = self.scored_pairs = 0

    def verify(self, claim, spans):
        return self.verify_many((claim, s) for s in spans)

    def verify_many(self, pairs):
        """Score (claim, full span) inputs across claims in caller order."""
        import torch

        results = []
        for batch in batched(pairs, self.batch_size):
            claims, spans = zip(*batch, strict=True)
            for claim, span in batch:
                string(claim)
                span.validate(self.corpus, full=True)
            claim_ids = self.tokenizer(
                list(claims), add_special_tokens=False, verbose=False
            )["input_ids"]
            special = self.tokenizer.num_special_tokens_to_add(pair=True)
            if any(not ids or len(ids) + special >= MAX_TOKENS for ids in claim_ids):
                raise ValueError("claim cannot fit intact with a premise token")
            inputs = self.tokenizer(
                [s.quote for s in spans],
                list(claims),
                padding=True,
                truncation="only_first",
                max_length=MAX_TOKENS,
                return_offsets_mapping=True,
                return_tensors="pt",
            )
            offsets = inputs.pop("offset_mapping").tolist()
            effective = []
            for i, span in enumerate(spans):
                ends = [
                    end
                    for (start, end), seq in zip(
                        offsets[i], inputs.sequence_ids(i), strict=True
                    )
                    if seq == 0 and end > start
                ]
                if not ends:
                    raise ValueError("premise has no retained text")
                end = max(ends)
                # Ignore unencoded trailing whitespace when reporting truncation counts.
                if span.quote[end:].strip():
                    self.truncated_pairs += 1
                effective.append(
                    replace(span, end=span.start + end, quote=span.quote[:end])
                )
            with torch.inference_mode():
                logits = self.network(**inputs.to(self.device)).logits.float()
                probabilities = logits.softmax(dim=-1).cpu().tolist()
            for span, probs in zip(effective, probabilities, strict=True):
                results.append(ScoredSpan(span, *_millionths(probs), self.model_digest))
            self.scored_pairs += len(batch)
        return tuple(results)


class ScoreCache:
    """Store scores with bindings to the exact requested and retained inputs."""

    def __init__(self, path, verifier):
        self.verifier = verifier
        self.corpus = verifier.corpus
        self.model_digest = verifier.model_digest
        self.db = sqlite3.connect(path, timeout=120)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS scores (key TEXT PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL)"
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.db.close()

    def _check(self, row, span):
        row.span.validate(self.corpus)
        if (
            row.model_digest != self.model_digest
            or row.span.source_id != span.source_id
            or row.span.sentence_id != span.sentence_id
            or row.span.end > span.end
        ):
            raise ValueError("cache score does not match requested input")
        return row

    def verify(self, claim, spans):
        return self.verify_many((claim, span) for span in spans)

    def verify_many(self, pairs):
        """Resolve hits first, then batch distinct misses across claims."""
        pairs = tuple(pairs)
        ordered, found, missing = [], {}, {}
        for claim, span in pairs:
            string(claim)
            span.validate(self.corpus, full=True)
            key = digest(
                [1, self.model_digest, self.corpus.digest, claim, span.to_dict()]
            )
            ordered.append(key)
            if key in found or key in missing:
                continue
            with closing(
                self.db.execute(
                    "SELECT payload,checksum FROM scores WHERE key=?", (key,)
                )
            ) as cur:
                saved = cur.fetchone()
            if saved is None:
                missing[key] = (claim, span)
                continue
            payload = json.loads(saved[0])
            if digest([key, payload]) != saved[1]:
                raise ValueError("cache checksum mismatch")
            found[key] = self._check(ScoredSpan.from_dict(payload), span)
        if missing:
            if hasattr(self.verifier, "verify_many"):
                rows = self.verifier.verify_many(missing.values())
            else:
                rows = (
                    self.verifier.verify(claim, (span,))[0]
                    for claim, span in missing.values()
                )
            with self.db:
                for (key, (_, span)), row in zip(missing.items(), rows, strict=True):
                    found[key] = self._check(row, span)
                    payload = row.to_dict()
                    encoded, checksum = (
                        canonical_bytes(payload).decode(),
                        digest([key, payload]),
                    )
                    self.db.execute(
                        "INSERT OR IGNORE INTO scores VALUES (?,?,?)",
                        (key, encoded, checksum),
                    )
                    stored = self.db.execute(
                        "SELECT checksum FROM scores WHERE key=?", (key,)
                    ).fetchone()
                    if stored[0] != checksum:
                        raise ValueError("conflicting cache results for same input")
        return tuple(found[key] for key in ordered)
