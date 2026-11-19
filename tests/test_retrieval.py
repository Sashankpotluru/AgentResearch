from pathlib import Path


def test_rank_read_and_challenge():
    from agentresearch.retrieval import BM25Index
    from agentresearch.schema import Corpus, load_json

    corpus = Corpus.from_dict(load_json(Path(__file__).parent / "fixtures/core.json"))
    idx = BM25Index(corpus)
    assert [h["source_id"] for h in idx.search("sea ice")] == ["a", "b", "c", "d", "e"]
    assert idx.search("ice", mode="challenge")[0]["source_id"] == "b"
    assert idx.search("unknownword") == []
    assert idx.search("not")[0]["source_id"] == "b"
    read = idx.read("however ice", "b")
    assert [s.sentence_id for s in read] == ["1", "0"]
    assert all(s.quote == corpus.by_id["b"].text[s.start : s.end] for s in read)


def test_focus_selects_four_distinct_terms():
    from agentresearch.retrieval import BM25Index
    from agentresearch.schema import Corpus, load_json

    corpus = Corpus.from_dict(load_json(Path(__file__).parent / "fixtures/core.json"))
    idx = BM25Index(corpus)
    # Four rare terms occur only in b. The ubiquitous fifth term pulls in other sources.
    query = "however did recover not ice"
    assert len(idx.search(query, "relevance")) == 5
    assert [h["source_id"] for h in idx.search(query, "focus")] == ["b"]
