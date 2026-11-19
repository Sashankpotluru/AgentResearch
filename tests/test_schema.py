import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures/core.json"


def test_corpus_and_exact_types():
    from agentresearch.schema import Corpus, RunConfig, canonical_bytes, load_json

    raw = load_json(FIXTURE)
    c = Corpus.from_dict(raw)
    assert c.to_dict() == raw
    with pytest.raises(ValueError):
        Corpus.from_dict({**raw, "sources": raw["sources"] * 2})
    with pytest.raises(ValueError):
        Corpus.from_dict({**raw, "schema_version": True})
    with pytest.raises(ValueError):
        Corpus.from_dict({**raw, "extra": False})
    for val in (False, 1.0, 2**53):
        with pytest.raises(ValueError):
            RunConfig("fixed", val, 700000, "fixture")
    with pytest.raises(ValueError):
        canonical_bytes({"x": 1.0})
    assert canonical_bytes({"b": None, "a": "é"}) == '{"a":"é","b":null}'.encode()


def test_import_rejects_duplicate_keys_invalid_offsets_and_oversize(tmp_path):
    from agentresearch.schema import Corpus, load_json

    path = tmp_path / "run.json"
    path.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError):
        load_json(path)
    with pytest.raises(ValueError):
        load_json(path, max_bytes=3)
    raw = json.loads(FIXTURE.read_text())
    raw["sources"][0]["sentences"][1]["start"] = False
    with pytest.raises(ValueError):
        Corpus.from_dict(raw)


def test_import_rejects_whitespace_only_sentence_among_valid_sentences():
    from agentresearch.schema import Corpus, load_json

    raw = load_json(FIXTURE)
    source = raw["sources"][0]
    start = len(source["text"])
    source["text"] += "\t\u2003"
    source["sentences"].append(
        {"sentence_id": "blank", "start": start, "end": start + 2}
    )
    with pytest.raises(ValueError, match="whitespace"):
        Corpus.from_dict(raw)
