"""Read-only corpus tools and validation of returned evidence."""

from typing import Protocol

from .schema import ScoredSpan, Span, integer, keys


class TransientToolError(Exception):
    """An explicitly retryable provider failure."""


class Verifier(Protocol):
    model_digest: str

    def verify(self, claim: str, spans: tuple[Span, ...]) -> tuple[ScoredSpan, ...]: ...


class CorpusTools:
    def __init__(self, corpus, index, verifier):
        if corpus is not index.corpus and corpus.digest != index.corpus.digest:
            raise ValueError("index corpus mismatch")
        self.corpus = corpus
        self.index = index
        self.verifier = verifier

    def search(self, claim, mode="relevance", limit=12):
        return self.index.search(claim, mode, limit)

    def read(self, claim, source_id):
        return self.index.read(claim, source_id)

    def verify(self, claim, spans):
        return self.verifier.verify(claim, spans)


def validate_output(tool, args, output, index, model_digest):
    corpus = index.corpus
    if type(output) is not list:
        raise ValueError("tool must return an array")
    if tool == "search":
        if len(output) > args["limit"]:
            raise ValueError("too many results")
        seen = set()
        for hit in output:
            keys(hit, ("source_id", "score", "snippet"))
            sid = hit["source_id"]
            integer(hit["score"], 0)
            if (
                sid in seen
                or sid not in corpus.by_id
                or hit["snippet"] != corpus.by_id[sid].text[:180]
            ):
                raise ValueError("search hit does not match corpus")
            seen.add(sid)
        if output != index.search(**args):
            raise ValueError("search results differ from corpus replay")
    elif tool == "read":
        source = corpus.by_id[args["source_id"]]
        if len(output) != min(2, len(source.sentences)):
            raise ValueError("incomplete read")
        seen = set()
        for item in output:
            s = Span.from_dict(item)
            s.validate(corpus, full=True)
            if s.source_id != source.source_id or s.sentence_id in seen:
                raise ValueError("read yielded unexpected or duplicate sentence")
            seen.add(s.sentence_id)
        if output != [s.to_dict() for s in index.read(**args)]:
            raise ValueError("read results differ from corpus replay")
    elif tool == "verify":
        if len(output) != len(args["spans"]):
            raise ValueError("incomplete verification")
        for item, requested in zip(output, args["spans"], strict=True):
            row = ScoredSpan.from_dict(item)
            row.span.validate(corpus)
            if (
                row.model_digest != model_digest
                or row.span.source_id != requested["source_id"]
                or row.span.sentence_id != requested["sentence_id"]
                or row.span.end > requested["end"]
            ):
                raise ValueError("score binding mismatch")
    else:
        raise ValueError("unknown tool")
