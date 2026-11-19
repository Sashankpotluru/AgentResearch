import io
import json
from http.client import IncompleteRead
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

XML = b"""<?xml version="1.0"?>
<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed.dtd">
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>
<ArticleTitle>Two <i>green</i> leaves</ArticleTitle><Abstract>
<AbstractText>First sentence. Next <i>part</i>!</AbstractText>
<AbstractText>Another section.</AbstractText></Abstract>
</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""


def http_mocks(monkeypatch, responses):
    from agentresearch import pubmed

    urls, waits = [], []

    def open_request(request, timeout):
        assert 0 < timeout <= 15
        urls.append(request.full_url)
        response = responses[len(urls) - 1]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, io.BytesIO):
            return response
        return io.BytesIO(response)

    monkeypatch.setattr(pubmed, "_open", open_request)
    monkeypatch.setattr(pubmed.time, "sleep", waits.append)
    monkeypatch.setattr(pubmed, "_next_request", 0)
    return urls, waits


def search(*ids):
    return json.dumps({"esearchresult": {"idlist": list(ids)}}).encode()


def test_pubmed_provenance_spans_and_pacing(monkeypatch):
    from agentresearch.pubmed import fetch_corpus

    urls, waits = http_mocks(monkeypatch, [search("123"), XML])
    corpus = fetch_corpus("green + leaf", max_records=2)
    (source,) = corpus.sources
    assert source.source_id == "pubmed:123" and source.url.endswith("/123/")
    assert source.title == "Two green leaves"
    assert [source.text[s.start : s.end] for s in source.sentences] == [
        "First sentence.",
        "Next part!",
        "Another section.",
    ]
    assert parse_qs(urlparse(urls[0]).query)["term"] == ["green + leaf"]
    assert all(urlparse(u).hostname == "eutils.ncbi.nlm.nih.gov" for u in urls)
    assert len(waits) >= 1 and waits[0] > 0.3


def test_pubmed_empty_results_and_limits(monkeypatch):
    from agentresearch.pubmed import fetch_corpus

    urls, _ = http_mocks(monkeypatch, [search()])
    assert fetch_corpus("nothing").sources == () and len(urls) == 1
    for query, maximum in [(" ", 1), ("x" * 1001, 1), ("ok", 51)]:
        with pytest.raises(ValueError):
            fetch_corpus(query, maximum)


def test_pubmed_transient_errors_are_bounded(monkeypatch):
    from agentresearch.pubmed import fetch_corpus

    temporary = HTTPError("https://example.org/", 429, "limited", {}, None)
    urls, _ = http_mocks(monkeypatch, [temporary] * 3)
    with pytest.raises(ValueError, match="429"):
        fetch_corpus("leaves")
    assert len(urls) == 3
    urls, _ = http_mocks(monkeypatch, [HTTPError("", 400, "bad", {}, None)])
    with pytest.raises(ValueError, match="400"):
        fetch_corpus("leaves")
    assert len(urls) == 1


@pytest.mark.parametrize(
    "responses",
    [
        [search("123"), XML.replace(b">123<", b">321<")],
        [
            search("123"),
            XML.replace(b"<PubmedArticleSet>", b"<!ENTITY invalid><PubmedArticleSet>"),
        ],
        [search("123"), b"<invalid"],
        [search("123", "124")],
        [search("wrong")],
        [b" " * (5 * 1024 * 1024 + 1)],
    ],
)
def test_rejects_malformed_oversized_and_unrequested_responses(monkeypatch, responses):
    from agentresearch.pubmed import fetch_corpus

    http_mocks(monkeypatch, responses)
    with pytest.raises(ValueError):
        fetch_corpus("leaves", 1)


class InterruptedTransfer(io.BytesIO):
    def read(self, size):
        raise IncompleteRead(b"partial", 25)


def test_pubmed_retries_interrupted_read_then_recovers(monkeypatch):
    from agentresearch.pubmed import fetch_corpus

    urls, _ = http_mocks(monkeypatch, [search("123"), InterruptedTransfer(), XML])
    assert fetch_corpus("leaves").sources[0].source_id == "pubmed:123"
    assert len(urls) == 3 and urls[1] == urls[2]


def test_pubmed_interrupted_read_exhausts_budget_and_exits_cleanly(monkeypatch, capsys):
    from agentresearch.cli import main

    urls, _ = http_mocks(monkeypatch, [InterruptedTransfer() for _ in range(3)])
    with pytest.raises(SystemExit) as error:
        main(["pubmed", "--query", "leaves"])
    assert error.value.code == 2 and len(urls) == 3
    assert "after three attempts" in capsys.readouterr().err
