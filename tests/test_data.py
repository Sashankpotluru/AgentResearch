import hashlib
import io
import json
import tarfile

import pytest


@pytest.fixture
def tiny(tmp_path, monkeypatch):
    from agentresearch import data

    docs = [
        {"doc_id": n, "title": str(n), "abstract": ["🌱 soil", "βeta."]} for n in (1, 2)
    ]

    def science(i, docs, evidence):
        return {
            "id": i,
            "claim": f" claim {i} ",
            "cited_doc_ids": docs,
            "evidence": evidence,
        }

    train = [
        science(1, [1], {}),
        science(2, [2], {"2": [{"label": "CONTRADICT", "sentences": [1]}]}),
    ]
    dev = [
        science(3, [1], {"1": [{"label": "SUPPORT", "sentences": [0]}]}),
        science(4, [1], {}),
    ]
    raw = tmp_path / "raw"
    raw.mkdir()
    with tarfile.open(raw / "scifact.tar.gz", "w:gz") as archive:
        for name, rows in [
            ("data/corpus.jsonl", docs),
            ("data/claims_train.jsonl", train),
            ("data/claims_dev.jsonl", dev),
            ("../unexpected", [{"trap": 1}]),
        ]:
            content = b"".join(json.dumps(r).encode() + b"\n" for r in rows)
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))

    def climate(k, label, eids):
        return {
            "claim_id": k,
            "claim": f"original {k}",
            "claim_label": label,
            "evidences": [
                {
                    "article": "Fiction",
                    "evidence_id": x,
                    "evidence": "🌳 " + x,
                    "evidence_label": "SUPPORTS",
                }
                for x in eids
            ],
        }

    rows = [
        climate("a", "DISPUTED", ["E2", "E1"]),
        climate("b", "NOT_ENOUGH_INFO", ["E2"]),
    ]
    (raw / "climate-fever.jsonl").write_text("\n".join(map(json.dumps, rows)))
    pins = {
        f.name: {
            "url": "https://example.org/" + f.name,
            "bytes": f.stat().st_size,
            "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
        }
        for f in raw.iterdir()
    }
    monkeypatch.setattr(data, "_source_pins", lambda: pins)
    return raw, tmp_path / "prepared"


def test_prepare_separates_labels_and_preserves_ids_offsets_clusters(tiny):
    from agentresearch.data import load_dataset, prepare

    raw, out = tiny
    manifest = prepare(raw, out)
    sci, calibration = load_dataset(out, "scifact", "calibration")
    _, dev = load_dataset(out, "scifact", "dev")
    climate, test = load_dataset(out, "climate", "test")
    assert not (out.parent / "unexpected").exists()
    assert [r["claim"] for r in calibration] == [" claim 2 "]
    assert [r["label"] for r in calibration + dev] == [
        "REFUTES",
        "SUPPORTS",
        "INSUFFICIENT",
    ]
    assert calibration[0]["evidence"] == [
        {"source_id": "scifact:2", "sentence_ids": ["1"], "label": "REFUTES"}
    ]
    assert dev[0]["cluster_id"] == dev[1]["cluster_id"]
    assert [r["label"] for r in test] == ["CONFLICT", "INSUFFICIENT"]
    assert test[0]["cluster_id"] == test[1]["cluster_id"]
    assert [r["claim"] for r in test] == ["original a", "original b"]
    source = climate.sources[0]
    assert len(climate.sources) == 1 and source.source_id.endswith(
        hashlib.sha256(b"Fiction").hexdigest()
    )
    assert [
        (s.sentence_id, source.text[s.start : s.end]) for s in source.sentences
    ] == [("E1", "🌳 E1"), ("E2", "🌳 E2")]
    assert set(sci.sources[0].to_dict()) == {
        "source_id",
        "sentences",
        "url",
        "text",
        "title",
    }
    assert "label" not in (out / "climate" / "corpus.jsonl").read_text()
    assert manifest["datasets"]["scifact"]["corpus_digest"] == sci.digest
    assert (out / "scifact" / "train.jsonl").exists()


def test_rejects_changed_raw_and_prepared_files(tiny):
    from agentresearch.data import load_dataset, prepare

    raw, out = tiny
    prepare(raw, out)
    with (out / "climate/test.jsonl").open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(ValueError, match="digest"):
        load_dataset(out, "climate", "test")
    with (raw / "scifact.tar.gz").open("ab") as handle:
        handle.write(b"x")
    with pytest.raises(ValueError, match="pin"):
        prepare(raw, out)
    with pytest.raises(ValueError):
        load_dataset(out, "../escape", "test")


def test_acquire_verifies_size_hash_and_reuses_only_matching_files(
    tmp_path, monkeypatch
):
    from agentresearch import data

    payload = b"valid release"
    pin = {
        "url": "https://example.org/release",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    monkeypatch.setattr(data, "_source_pins", lambda: {"release": pin})
    responses = iter([b"wrong release", payload])
    monkeypatch.setattr(
        data, "urlopen", lambda url, timeout: io.BytesIO(next(responses)), raising=False
    )
    with pytest.raises(ValueError, match="pin"):
        data.acquire(tmp_path)
    assert not (tmp_path / "release").exists()
    data.acquire(tmp_path)
    data.acquire(tmp_path)
    assert (tmp_path / "release").read_bytes() == payload


def test_published_data_pins_bind_exact_release_bytes():
    from agentresearch.data import _source_pins

    assert {k: v["sha256"] for k, v in _source_pins().items()} == {
        "scifact.tar.gz": "11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be",
        "climate-fever.jsonl": "8a4b9032d861be482ffb49dddfd283ffa6089e654f1e968040011882c5eb6e0b",
    }


def test_prepared_query_schema_rejects_string_in_place_of_sentence_id_list(tiny):
    from agentresearch.data import load_dataset, prepare

    raw, out = tiny
    manifest = prepare(raw, out)
    query = out / "scifact/calibration.jsonl"
    row = json.loads(query.read_bytes())
    row["evidence"][0]["sentence_ids"] = "1"
    query.write_text(json.dumps(row) + "\n")
    manifest["datasets"]["scifact"]["splits"]["calibration"]["sha256"] = hashlib.sha256(
        query.read_bytes()
    ).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="evidence"):
        load_dataset(out, "scifact", "calibration")
