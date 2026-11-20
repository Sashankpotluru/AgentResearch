import math

import pytest

from agentresearch.cyber_retrieval import (
    CharacterIndex,
    attack_rankings,
    fuse,
    metrics,
    tram_rankings,
)
from agentresearch.cyber_verify import check_rankings, ranking_statistics
from agentresearch.data import source_from_lines
from agentresearch.schema import Corpus


def test_character_ties_zero_overlap_and_fusion():
    index = CharacterIndex(
        {"T2": "IDENTICAL note", "T1": "identical   note", "T3": "other"}
    )
    assert index.rank("note") == ["T1", "T2"]
    assert index.rank("zzzz") == [] and index.rank("a") == []
    assert fuse([["T2", "T1"], ["T1", "T2"]]) == ["T1", "T2"]


def test_family_filter_precedes_cut_and_rankings_do_not_see_labels():
    corpus = Corpus(
        tuple(source_from_lines(f"S{i}", "note", [("p", "note")]) for i in range(9))
    )
    catalog = {
        "excerpts": {
            str(i): {
                "source_id": f"S{i}",
                "relationships": [{"family_id": "target" if i == 8 else "other"}],
            }
            for i in range(9)
        },
        "queries": [
            {
                "query_id": "q",
                "query": "note",
                "family_id": "target",
                "ransomware_related": False,
                "gold": ["S8"],
            }
        ],
    }
    ranks = attack_rankings(corpus, catalog)[0]["rankings"]
    assert ranks["bm25"] == ["S0", "S1", "S2", "S3", "S4"] and ranks["family_bm25"] == [
        "S8"
    ]
    defs = {
        "T1234": {"name": "note", "description": "incident text"},
        "T9999": {"name": "file", "description": "event summary"},
    }
    item = {
        "query_id": "q",
        "sentence": "T1234",
        "gold": ["T1234"],
        "component": "ransom",
        "ransomware_related": True,
    }
    ranks = tram_rankings(defs, [item])[0]["rankings"]
    assert ranks == {"bm25": [], "char_tfidf": [], "rrf": []}
    assert (
        tram_rankings(defs, [{**item, "gold": ["T9999"], "component": "note"}])[0][
            "rankings"
        ]
        == ranks
    )


def test_manual_metrics_and_invalid_candidate():
    ranked = ["c", "a", "d", "b"]
    gold = ["a", "b", "e"]
    scores = metrics(ranked, gold)
    assert (
        scores["recall@5"] == 2 / 3 and scores["hit@1"] == 0 and scores["mrr@5"] == 0.5
    )
    assert scores["ndcg@5"] == pytest.approx(
        (1 / math.log2(3) + 1 / math.log2(5)) / (1 + 1 / math.log2(3) + 1 / 2)
    )
    row = {
        "query_id": "q",
        "rankings": {"bm25": ranked, "other": ["a"]},
        "gold": gold,
        "cluster": "x",
        "ransomware_related": True,
    }
    result = ranking_statistics(
        [row], ["bm25", "other"], "other", {"bootstrap": {"seed": 17, "draws": 10}}
    )
    assert result["all"]["metrics"]["bm25"] == pytest.approx(scores)
    with pytest.raises(ValueError, match="invalid ranking"):
        check_rankings([row], {"q": (gold, "x", True)}, set("abce"), ["bm25", "other"])
