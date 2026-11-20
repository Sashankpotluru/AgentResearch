import pytest

from agentresearch.cyber_data import attack_data, protocol, selection, sha, tram_data
from agentresearch.cyber_verify import verify_selection


def obj(kind, ident, external, name):
    return {
        "type": kind,
        "id": ident,
        "name": name,
        "description": "Ordinary source description.",
        "external_references": [
            {"source_name": "mitre-attack", "external_id": external}
        ],
    }


def test_active_provenance_unicode_spans_and_missing_reference():
    family = obj("malware", "malware--a", "S1", "Example")
    tech = obj("attack-pattern", "attack-pattern--a", "T1", "Unmentioned label")
    hist = obj("attack-pattern", "attack-pattern--b", "T2", "Historical")
    hist["revoked"] = True

    def rel(rid, text, urls):
        return {
            "id": rid,
            "type": "relationship",
            "relationship_type": "uses",
            "source_ref": family["id"],
            "target_ref": tech["id"],
            "description": text,
            "external_references": [
                {
                    "source_name": "Report",
                    "description": "Real report title",
                    "url": "https://example.org/" + u,
                }
                for u in urls
            ],
        }

    a, b, c = (
        rel("a", "Example  left a note 🧊. (Citation: A)", ["z"]),
        rel("b", "Example left a note 🧊.", ["a"]),
        rel("c", "Another behavior.", []),
    )
    corpus, catalog = attack_data(
        [family, tech, hist, a, b, c, {**c, "revoked": True, "id": "d"}], {"T2"}
    )
    assert len(corpus.sources) == 2
    merged = next(s for s in corpus.sources if s.url == "https://example.org/a")
    assert (
        merged.title == "Real report title" and merged.text == "Example left a note 🧊."
    )
    sent = merged.sentences[0]
    assert merged.text[sent.start : sent.end] == merged.text
    assert [
        r["id"] for r in catalog["excerpts"][sent.sentence_id]["relationships"]
    ] == ["a", "b"]
    assert catalog["techniques"]["T2"]["historical"]
    assert {s.url for s in corpus.sources} == {
        "https://example.org/a",
        "https://attack.mitre.org/software/S1/",
    }
    assert catalog["queries"][0]["gold"] == sorted(s.source_id for s in corpus.sources)
    assert all(
        "Unmentioned label" not in s.text + s.title and "T1" not in s.text + s.title
        for s in corpus.sources
    )


def test_tram_transitive_components():
    rows = [
        {"sentence": text, "labels": labels, "doc_title": doc}
        for text, labels, doc in [
            ("One note", ["T1"], "A"),
            ("One  note", ["T2"], "B ransom"),
            ("Two notes", ["T2"], "B ransom"),
            ("Two notes", ["T1"], "C"),
            ("Other", ["T1"], "D"),
            ("Unused", [], "X"),
            (" ", ["T2"], "X"),
        ]
    ]
    items = tram_data(rows, {"T1": {}, "T2": {}})
    by = {q["sentence"]: q for q in items}
    assert by["One note"]["rows"] == [0, 1] and by["One note"]["gold"] == ["T1", "T2"]
    assert (
        by["One note"]["reports"] == ["A", "B ransom"]
        and by["One note"]["ransomware_related"]
    )
    assert by["Two notes"]["component"] == by["One note"]["component"] == "A"
    assert len(items) == 3 and by["Other"]["component"] == "D"
    with pytest.raises(ValueError, match="unknown TRAM"):
        tram_data(rows, {"T1": {}})


def test_selection_hash_domains_priority_and_fallback():
    queries = [
        {
            "query_id": f"S{i}-T{j}",
            "family_id": f"S{i}",
            "family": "WannaCry" if i == 1 else f"Family {i}",
            "ransomware_related": i == 0,
        }
        for i in range(20)
        for j in range(4)
    ]
    p = protocol()
    got = selection(list(reversed(queries)), p)
    assert got == verify_selection(queries, p)
    assert len(got["benchmark"]) == 64 and len(set(got["demo"])) == 20
    assert got["demo"][0] == min((f"S1-T{i}" for i in range(4)), key=sha)
    assert got["benchmark"][0] == min(
        (f"S0-T{i}" for i in range(4)), key=lambda q: sha(p["experiment"] + ":" + q)
    )
