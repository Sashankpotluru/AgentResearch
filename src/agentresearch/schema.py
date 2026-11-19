"""Versioned public records and portable, integer-only JSON."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields
from functools import cached_property
from pathlib import Path
from types import MappingProxyType

MAX_INT = 2**53 - 1
POLICIES = ("fixed", "first_hit", "challenge", "guarded")
VERDICTS = ("SUPPORTS", "REFUTES", "CONFLICT", "INSUFFICIENT", "ABSTAIN")


def integer(x, lo=-MAX_INT, hi=MAX_INT):
    if type(x) is not int or not lo <= x <= hi:
        raise ValueError("integer outside permitted range")
    return x


def string(x, *, empty=False):
    if type(x) is not str or (not empty and not x.strip()):
        raise ValueError("expected text")
    x.encode("utf-8")
    return x


def keys(obj, names):
    if type(obj) is not dict or set(obj) != set(names):
        raise ValueError("missing or unknown object fields")


def _json_value(v):
    if v is None or type(v) is bool:
        return
    if type(v) is int:
        integer(v)
    elif type(v) is str:
        string(v, empty=True)
    elif type(v) is list:
        for item in v:
            _json_value(item)
    elif type(v) is dict:
        for key, item in v.items():
            string(key, empty=True)
            _json_value(item)
    else:
        raise ValueError("unsupported JSON value")


def canonical_bytes(value):
    _json_value(value)
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _pairs(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


def load_json(path, max_bytes=5 * 1024 * 1024):
    integer(max_bytes, 1)
    with Path(path).open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("JSON file exceeds size limit")
    obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    _json_value(obj)
    if type(obj) is not dict:
        raise ValueError("expected JSON object")
    return obj


def _list(value):
    if type(value) is not list:
        raise ValueError("expected array")
    return value


def _dump(value):
    if isinstance(value, Record):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_dump(item) for item in value]
    return value


class Record:
    def to_dict(self):
        return {f.name: _dump(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, obj):
        keys(obj, (f.name for f in fields(cls)))
        return cls(**obj)


@dataclass(frozen=True)
class Sentence(Record):
    sentence_id: str
    start: int
    end: int

    def __post_init__(self):
        string(self.sentence_id)
        integer(self.start, 0)
        integer(self.end, self.start + 1)


@dataclass(frozen=True)
class Source(Record):
    source_id: str
    title: str
    text: str
    sentences: tuple[Sentence, ...]
    url: str | None = None

    def __post_init__(self):
        string(self.source_id)
        string(self.title, empty=True)
        string(self.text)
        if self.url is not None:
            string(self.url)
        if type(self.sentences) is not tuple or not self.sentences:
            raise ValueError("expected source sentences")
        last_end = 0
        seen = set()
        for sent in self.sentences:
            if (
                not isinstance(sent, Sentence)
                or sent.sentence_id in seen
                or not last_end <= sent.start < sent.end <= len(self.text)
            ):
                raise ValueError("duplicate, overlapping, or out-of-range sentence")
            if not self.text[sent.start : sent.end].strip():
                raise ValueError("whitespace-only sentence")
            seen.add(sent.sentence_id)
            last_end = sent.end

    @cached_property
    def source_digest(self):
        return digest(self.to_dict())

    @cached_property
    def by_sentence(self):
        return MappingProxyType({s.sentence_id: s for s in self.sentences})

    @classmethod
    def from_dict(cls, obj):
        keys(obj, (f.name for f in fields(cls)))
        return cls(
            **{
                **obj,
                "sentences": tuple(
                    Sentence.from_dict(s) for s in _list(obj["sentences"])
                ),
            }
        )


@dataclass(frozen=True)
class Corpus(Record):
    sources: tuple[Source, ...]

    def __post_init__(self):
        if type(self.sources) is not tuple or not all(
            isinstance(s, Source) for s in self.sources
        ):
            raise ValueError("expected source tuple")
        if len(self.by_id) != len(self.sources):
            raise ValueError("duplicate source ID")

    @cached_property
    def by_id(self):
        return MappingProxyType({s.source_id: s for s in self.sources})

    @cached_property
    def digest(self):
        return digest(self.to_dict())

    def to_dict(self):
        return {"schema_version": 1, "sources": [s.to_dict() for s in self.sources]}

    @classmethod
    def from_dict(cls, obj):
        keys(obj, ("schema_version", "sources"))
        integer(obj["schema_version"], 1, 1)
        return cls(tuple(Source.from_dict(s) for s in _list(obj["sources"])))


@dataclass(frozen=True)
class Span(Record):
    source_id: str
    sentence_id: str
    start: int
    end: int
    quote: str
    source_digest: str

    def __post_init__(self):
        Sentence(self.sentence_id, self.start, self.end)
        string(self.source_id)
        string(self.quote)
        if len(self.quote) != self.end - self.start:
            raise ValueError("quote length does not match offsets")
        if not re.fullmatch("[0-9a-f]{64}", string(self.source_digest)):
            raise ValueError("invalid source digest")

    @cached_property
    def identity(self):
        return digest(self.to_dict())

    def validate(self, corpus, *, full=False):
        source = corpus.by_id.get(self.source_id)
        sent = source.by_sentence.get(self.sentence_id) if source else None
        if (
            sent is None
            or self.source_digest != source.source_digest
            or self.start != sent.start
            or self.end > sent.end
            or (full and self.end != sent.end)
            or self.quote != source.text[self.start : self.end]
        ):
            raise ValueError("span does not match source")


@dataclass(frozen=True)
class ScoredSpan(Record):
    span: Span
    contradiction: int
    entailment: int
    neutral: int
    model_digest: str

    def __post_init__(self):
        if not isinstance(self.span, Span):
            raise ValueError("expected span")  # noqa: TRY004 -- record validation error
        if (
            sum(
                integer(v, 0, 1000000)
                for v in (self.contradiction, self.entailment, self.neutral)
            )
            != 1000000
        ):
            raise ValueError("scores must sum to one million")
        string(self.model_digest)

    @classmethod
    def from_dict(cls, obj):
        keys(obj, (f.name for f in fields(cls)))
        return cls(**{**obj, "span": Span.from_dict(obj["span"])})


@dataclass(frozen=True)
class RunConfig(Record):
    policy: str
    budget: int
    threshold: int
    model_digest: str

    def __post_init__(self):
        if self.policy not in POLICIES:
            raise ValueError("unknown policy")
        integer(self.budget, 1, 8)
        integer(self.threshold, 0, 1000000)
        string(self.model_digest)


@dataclass(frozen=True)
class Decision(Record):
    verdict: str
    witnesses: tuple[str, ...]
    internal_conflict: bool

    def __post_init__(self):
        if self.verdict not in VERDICTS or type(self.internal_conflict) is not bool:
            raise ValueError("invalid decision")
        if type(self.witnesses) is not tuple or any(
            not re.fullmatch("[0-9a-f]{64}", string(w)) for w in self.witnesses
        ):
            raise ValueError("invalid witnesses")

    @classmethod
    def from_dict(cls, obj):
        keys(obj, (f.name for f in fields(cls)))
        return cls(**{**obj, "witnesses": tuple(_list(obj["witnesses"]))})
