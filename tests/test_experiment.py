from copy import deepcopy

from test_engine import components

from agentresearch import experiment as exp
from agentresearch.engine import Investigator
from agentresearch.schema import RunConfig
from agentresearch.store import RunStore


def sample():
    row = {
        "query_id": "11",
        "cluster_id": "11",
        "claim": "ice",
        "label": "SUPPORTS",
        "cited_source_ids": ["a"],
        "evidence": [
            {"source_id": "a", "sentence_ids": ["0", "1"], "label": "SUPPORTS"}
        ],
    }
    tools = components()
    with RunStore(":memory:") as store:
        run = Investigator(tools, store).run(
            "ice", RunConfig("fixed", 4, 700000, "fixture"), "11"
        )
    return tools, row, run


def test_observation_checks_complete_nonwhitespace_and_groups():
    tools, row, run = sample()
    ids = exp.source_ordinals(tools.corpus)
    original = exp.observation(row, run, tools.corpus, ids)
    assert (
        original["gold_sentences"] == 2
        and original["found_sentences"] == 2
        and original["complete"] == 1
    )
    shortened = deepcopy(run)
    s = next(x["span"] for x in shortened["scores"] if x["span"]["source_id"] == "a")
    s["end"] -= 1
    s["quote"] = s["quote"][:-1]
    revised = exp.observation(row, shortened, tools.corpus, ids)
    assert (
        revised["found_sentences"] == 1
        and revised["complete"] == 0
        and revised["intact"] == revised["checked"]
    )
    assert "ice" not in str(original)


def test_frozen_protocol_and_site_source_union():
    from agentresearch.data import file_digest

    p = exp.protocol_path()
    assert (
        file_digest(p)
        == "e5e11ae082d3a179d1c0fe55b79f059b482bfb000266a2364fc15774e2ea0ed2"
    )
    protocol = exp.read(p)
    assert protocol["calibration"]["grid"] == [600000, 700000, 800000, 900000]
    assert (
        protocol["budgets"] == [4, 8]
        and protocol["datasets"]["scifact"]["splits"]["calibration"]["queries"] == 537
    )
    tools, row, run = sample()
    data = exp.demo_claim(row, {"fixed-4": run}, tools.corpus)
    hits = {
        h["source_id"]
        for e in run["events"]
        if e["tool"] == "search"
        for h in e["output"]
    }
    assert set(data["sources"]) == hits


def test_prefetch_scores_only_union_of_actual_branches():
    from agentresearch.data import source_from_lines
    from agentresearch.schema import Corpus, ScoredSpan, Span

    corpus = Corpus(
        tuple(source_from_lines(str(i), str(i), [("0", "text")]) for i in range(30))
    )

    class Index:
        def search(self, claim, mode="relevance"):
            ids = (
                range(12)
                if mode == "relevance"
                else range(12, 24)
                if mode == "challenge"
                else range(18, 30)
            )
            return [{"source_id": str(i)} for i in ids]

        def read(self, claim, sid):
            s = corpus.by_id[sid]
            return (Span(sid, "0", 0, 4, "text", s.source_digest),)

    class Scorer:
        def __init__(self):
            self.requested = []

        def verify_many(self, pairs):
            self.requested.extend((q, int(s.source_id)) for q, s in pairs)
            return tuple(
                ScoredSpan(s, 10000, e, 990000 - e, "fixture")
                for q, s in pairs
                for e in [980000 if q == "supports" else 10000]
            )

    v = Scorer()
    assert exp.prefetch(["supports", "neutral"], Index(), v, threshold=700000) == 26
    assert {i for q, i in v.requested if q == "supports"} == set(range(8)) | set(
        range(12, 17)
    )
    assert {i for q, i in v.requested if q == "neutral"} == set(range(8)) | set(
        range(18, 23)
    )
