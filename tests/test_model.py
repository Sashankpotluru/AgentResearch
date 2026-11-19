import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agentresearch.schema import Corpus, ScoredSpan, Sentence, Source, Span


def source_fixture():
    text = "🌱 α " + "soil " * 550 + "END"
    source = Source("s", "", text, (Sentence("x", 0, len(text)),))
    return Corpus((source,)), Span("s", "x", 0, len(text), text, source.source_digest)


def test_refuses_corrupt_lock_and_extra_model_files(tmp_path):
    from agentresearch.data import pin_path
    from agentresearch.model import check_model

    lock = json.loads(pin_path("model-lock.json").read_text())
    supplied = tmp_path / "override.json"
    lock["files"]["config.json"]["sha256"] = "e" * 64
    supplied.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="lock"):
        check_model(tmp_path, supplied)
    for name in lock["files"]:
        (tmp_path / name).touch()
    with pytest.raises(ValueError, match="unexpected"):
        check_model(tmp_path, pin_path("model-lock.json"))
    supplied.unlink()
    with pytest.raises(ValueError, match="checksum"):
        check_model(tmp_path, pin_path("model-lock.json"))


def test_neural_retains_unicode_prefix_and_complete_claim():
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from tokenizers.processors import TemplateProcessing

    from agentresearch.model import NeuralVerifier

    vocab = {
        word: i
        for i, word in enumerate(
            ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "🌱", "α", "soil", "END", "claim", "β"]
        )
    }
    tokenizer = Tokenizer(WordLevel(vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.post_processor = TemplateProcessing(
        single="[CLS] $A [SEP]",
        pair="[CLS] $A [SEP] $B:1 [SEP]:1",
        special_tokens=[("[CLS]", 2), ("[SEP]", 3)],
    )
    fast = transformers.PreTrainedTokenizerFast(
        tokenizer_object=tokenizer, pad_token="[PAD]", unk_token="[UNK]"
    )
    corpus, span = source_fixture()
    seen = []

    class Logits:
        def __call__(self, **inputs):
            seen.extend(inputs["input_ids"].tolist())
            return SimpleNamespace(
                logits=torch.tensor([[1.0, 3.0, 0.0]] * len(inputs["input_ids"]))
            )

    verifier = object.__new__(NeuralVerifier)
    verifier.corpus, verifier.tokenizer, verifier.network = corpus, fast, Logits()
    verifier.device, verifier.batch_size, verifier.model_digest = "cpu", 2, "neural"
    verifier.truncated_pairs = verifier.scored_pairs = 0
    (row,) = verifier.verify("claim β", (span,))
    assert len(seen[0]) == 512
    assert seen[0][-3:] == [8, 9, 3]
    assert row.span.quote == "🌱 α " + "soil " * 504 + "soil"
    row.span.validate(corpus)
    assert row.entailment > row.contradiction > row.neutral
    assert row.entailment + row.contradiction + row.neutral == 1_000_000
    assert verifier.truncated_pairs == 1
    with pytest.raises(ValueError, match="claim"):
        verifier.verify("claim " * 509, (span,))
    with pytest.raises(ValueError, match="source"):
        verifier.verify("claim", (replace(span, source_digest="0" * 64),))


def test_cache_reuses_exact_input_detects_corruption_and_is_durable(tmp_path):
    from agentresearch.model import ScoreCache

    corpus, span = source_fixture()

    class Counting:
        model_digest = "neural"

        def __init__(self):
            self.corpus, self.calls = corpus, []

        def verify_many(self, pairs):
            self.calls.extend(pairs)
            return tuple(
                ScoredSpan(s, 100, 900000, 99900, self.model_digest) for _, s in pairs
            )

    model = Counting()
    cache_path = tmp_path / "scores.db"
    with ScoreCache(cache_path, model) as cache:
        rows = cache.verify_many([("A", span), ("B", span), ("A", span)])
        assert len(rows) == 3 and rows[0] == rows[2] and len(model.calls) == 2
    with ScoreCache(cache_path, model) as cache:
        assert cache.verify("A", (span,)) == (rows[0],)
        assert len(model.calls) == 2
    with sqlite3.connect(cache_path) as db:
        db.execute("UPDATE scores SET payload=replace(payload,'900000','900001')")
    with (
        ScoreCache(cache_path, model) as cache,
        pytest.raises(ValueError, match="cache"),
    ):
        cache.verify("A", (span,))
