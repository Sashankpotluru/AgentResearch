"""Prepare the two pinned datasets. Runtime corpora contain source material only."""

import hashlib
import json
import tarfile
from collections import Counter, defaultdict
from importlib.metadata import distribution
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

from .schema import (
    Corpus,
    Sentence,
    Source,
    canonical_bytes,
    integer,
    keys,
    load_json,
    string,
)
from .transport import verified_context

SPLITS = {"scifact": ("train", "calibration", "dev"), "climate": ("test",)}
LABELS = {
    "SUPPORT": "SUPPORTS",
    "CONTRADICT": "REFUTES",
    "SUPPORTS": "SUPPORTS",
    "REFUTES": "REFUTES",
    "NOT_ENOUGH_INFO": "INSUFFICIENT",
    "DISPUTED": "CONFLICT",
}


def pin_path(name):
    module = Path(__file__).resolve()
    repo_directory = (
        "experiments"
        if name
        in {"protocol.json", "cyber-protocol.json", "cyber-diagnostic-protocol.json"}
        else "data"
    )
    checkout = module.parents[2] / repo_directory / name
    if module.parents[1].name == "src" and checkout.is_file():
        return checkout
    dist = distribution("agentresearch")
    relative = Path("share", "agentresearch", name)
    # pip --target relocates the data directory alongside the import package.
    target = Path(dist.locate_file(relative))
    if target.is_file():
        return target
    # Prefix and --user schemes record the data path relative to site-packages.
    for entry in dist.files or ():
        if entry.parts[-3:] == relative.parts and (path := entry.locate()).is_file():
            return path
    raise FileNotFoundError(f"missing installed AgentResearch resource: {name}")


def _source_pins():
    return load_json(pin_path("sources.json"))


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def acquire(raw_dir):
    """Populate a local directory from the published pins, verifying before rename."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name, pin in _source_pins().items():
        target = raw_dir / name
        if target.exists():
            if (
                target.stat().st_size != pin["bytes"]
                or file_digest(target) != pin["sha256"]
            ):
                raise ValueError(f"existing file differs from raw data pin: {name}")
            continue
        with urlopen(pin["url"], timeout=30, context=verified_context()) as response:
            content = response.read(pin["bytes"] + 1)
        if (
            len(content) != pin["bytes"]
            or hashlib.sha256(content).hexdigest() != pin["sha256"]
        ):
            raise ValueError(f"download differs from raw data pin: {name}")
        with NamedTemporaryFile(dir=raw_dir, delete=False) as tmp:
            try:
                tmp.write(content)
                tmp.close()
                Path(tmp.name).replace(target)
            finally:
                Path(tmp.name).unlink(missing_ok=True)


def source_from_lines(source_id, title, lines, url=None):
    parts, sentences, offset = [], [], 0
    for sentence_id, text in lines:
        sentences.append(Sentence(str(sentence_id), offset, offset + len(text)))
        parts.append(text)
        offset += len(text) + 1
    return Source(source_id, title, "\n".join(parts), tuple(sentences), url)


def _rows(stream):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate JSONL key")
        return result

    line_limit = 5 * 1024 * 1024
    while line := stream.readline(line_limit + 1):
        if len(line) > line_limit:
            raise ValueError("oversized JSONL record")
        row = json.loads(line, object_pairs_hook=unique)
        if not isinstance(row, dict):
            raise ValueError("expected JSONL object")  # noqa: TRY004 -- malformed input data
        yield row


def _write_rows(path, rows):
    with NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        try:
            for row in rows:
                tmp.write(canonical_bytes(row) + b"\n")
            tmp.close()
            Path(tmp.name).replace(path)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return file_digest(path)


def _clusters(rows, edges):
    """Assign the minimum original claim ID of each connected component."""
    parent = {row["query_id"]: row["query_id"] for row in rows}
    if len(parent) != len(rows):
        raise ValueError("duplicate claim ID")

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    owners = {}
    for row, members in zip(rows, edges, strict=True):
        q = row["query_id"]
        for member in members:
            a, b = root(q), root(owners.setdefault(member, q))
            parent[max(a, b)] = min(a, b)
    for row in rows:
        row["cluster_id"] = root(row["query_id"])
    return rows


def _sci_query(raw):
    evidence = [
        {
            "source_id": "scifact:" + doc,
            "label": LABELS[group["label"]],
            "sentence_ids": [str(i) for i in group["sentences"]],
        }
        for doc, groups in raw["evidence"].items()
        for group in groups
    ]
    labels = {e["label"] for e in evidence}
    if len(labels) > 1:
        raise ValueError("inconsistent SciFact claim labels")
    return {
        "query_id": str(raw["id"]),
        "claim": raw["claim"],
        "label": next(iter(labels), "INSUFFICIENT"),
        "evidence": evidence,
        "cited_source_ids": [f"scifact:{x}" for x in raw["cited_doc_ids"]],
    }


def _scifact(path):
    with tarfile.open(path, "r:gz") as archive:

        def read(name):
            member = archive.getmember(f"data/{name}.jsonl")
            if not member.isfile() or member.size > 64 * 1024 * 1024:
                raise ValueError("invalid archive member")
            with archive.extractfile(member) as stream:
                return list(_rows(stream))

        corpus = Corpus(
            tuple(
                source_from_lines(
                    f"scifact:{r['doc_id']}", r["title"], enumerate(r["abstract"])
                )
                for r in read("corpus")
            )
        )
        dev = [_sci_query(r) for r in read("claims_dev")]
        train = [_sci_query(r) for r in read("claims_train")]
    excluded_ids = {sid for row in dev for sid in row["cited_source_ids"]}
    calibration = [
        dict(r) for r in train if excluded_ids.isdisjoint(r["cited_source_ids"])
    ]
    splits = {"train": train, "calibration": calibration, "dev": dev}
    for rows in splits.values():
        _clusters(rows, (r["cited_source_ids"] for r in rows))
    return corpus, splits


def _climate(path):
    sentences, articles, rows, edges = {}, defaultdict(dict), [], []
    with path.open("rb") as stream:
        for r in _rows(stream):
            evidence, cited, ids = [], set(), []
            for e in r["evidences"]:
                title, eid, text = e["article"], e["evidence_id"], e["evidence"]
                if sentences.setdefault(eid, (title, text)) != (title, text):
                    raise ValueError("inconsistent Climate evidence identity")
                articles[title][eid] = text
                sid = "climate:" + hashlib.sha256(title.encode()).hexdigest()
                cited.add(sid)
                ids.append(eid)
                evidence.append(
                    {
                        "source_id": sid,
                        "sentence_ids": [eid],
                        "label": LABELS[e["evidence_label"]],
                    }
                )
            rows.append(
                {
                    "query_id": str(r["claim_id"]),
                    "claim": r["claim"],
                    "label": LABELS[r["claim_label"]],
                    "evidence": evidence,
                    "cited_source_ids": sorted(cited),
                }
            )
            edges.append(ids)
    sources = [
        source_from_lines(
            "climate:" + hashlib.sha256(title.encode()).hexdigest(),
            title,
            sorted(items.items()),
        )
        for title, items in sorted(articles.items())
    ]
    return Corpus(tuple(sources)), {"test": _clusters(rows, edges)}


def prepare(raw_dir: Path, out_dir: Path) -> dict:
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    pins = _source_pins()
    for name, pin in pins.items():
        path = raw_dir / name
        if path.stat().st_size != pin["bytes"] or file_digest(path) != pin["sha256"]:
            raise ValueError(f"raw data pin mismatch for {name}")
    manifest = {"schema_version": 1, "sources": pins, "datasets": {}}
    for name, (corpus, splits) in (
        ("scifact", _scifact(raw_dir / "scifact.tar.gz")),
        ("climate", _climate(raw_dir / "climate-fever.jsonl")),
    ):
        directory = out_dir / name
        directory.mkdir(parents=True, exist_ok=True)
        corpus_hash = _write_rows(
            directory / "corpus.jsonl", (s.to_dict() for s in corpus.sources)
        )
        info = {
            "corpus_digest": corpus.digest,
            "corpus_sha256": corpus_hash,
            "sources": len(corpus.sources),
            "sentences": sum(len(s.sentences) for s in corpus.sources),
            "splits": {},
        }
        for split, rows in splits.items():
            counts = Counter(r["cluster_id"] for r in rows)
            info["splits"][split] = {
                "sha256": _write_rows(directory / f"{split}.jsonl", rows),
                "queries": len(rows),
                "clusters": len(counts),
                "largest_cluster": max(counts.values(), default=0),
                "labels": dict(Counter(r["label"] for r in rows)),
            }
        manifest["datasets"][name] = info
    _write_rows(out_dir / "manifest.json", [manifest])
    return manifest


def read_corpus(path):
    path = Path(path)
    if path.suffix != ".jsonl":
        return Corpus.from_dict(load_json(path, max_bytes=256 * 1024 * 1024))
    if path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("oversized corpus")
    with path.open("rb") as stream:
        return Corpus(tuple(Source.from_dict(r) for r in _rows(stream)))


def load_dataset(prepared_dir, name, split):
    if name not in SPLITS or split not in SPLITS[name]:
        raise ValueError("unknown dataset or split")
    root = Path(prepared_dir)
    manifest = load_json(root / "manifest.json")
    keys(manifest, ("schema_version", "sources", "datasets"))
    integer(manifest["schema_version"], 1, 1)
    if manifest["sources"] != _source_pins():
        raise ValueError("prepared data provenance mismatch")
    info = manifest["datasets"][name]
    corpus_path, query_path = (
        root / name / "corpus.jsonl",
        root / name / f"{split}.jsonl",
    )
    if (
        file_digest(corpus_path) != info["corpus_sha256"]
        or file_digest(query_path) != info["splits"][split]["sha256"]
    ):
        raise ValueError("prepared file digest mismatch")
    corpus = read_corpus(corpus_path)
    if corpus.digest != info["corpus_digest"]:
        raise ValueError("corpus digest mismatch")
    if query_path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("oversized query file")
    with query_path.open("rb") as stream:
        rows = list(_rows(stream))
    for r in rows:
        keys(
            r,
            (
                "query_id",
                "claim",
                "label",
                "evidence",
                "cited_source_ids",
                "cluster_id",
            ),
        )
        for field in ("query_id", "claim", "cluster_id"):
            string(r[field])
        if (
            r["label"] not in LABELS.values()
            or not isinstance(r["evidence"], list)
            or not isinstance(r["cited_source_ids"], list)
            or not all(
                isinstance(s, str) and s in corpus.by_id for s in r["cited_source_ids"]
            )
        ):
            raise ValueError("invalid query label or cited source")
        for e in r["evidence"]:
            keys(e, ("source_id", "sentence_ids", "label"))
            source = corpus.by_id.get(e["source_id"])
            if (
                source is None
                or e["label"] not in LABELS.values()
                or not isinstance(e["sentence_ids"], list)
                or not e["sentence_ids"]
                or not all(
                    isinstance(s, str) and s in source.by_sentence
                    for s in e["sentence_ids"]
                )
            ):
                raise ValueError("invalid query evidence")
    return corpus, rows
