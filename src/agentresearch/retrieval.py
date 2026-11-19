"""Deterministic BM25 retrieval and corpus-IDF sentence selection."""

import math
import re
from collections import Counter, defaultdict

from .schema import Span, integer, string

STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "from",
        "by",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "that",
        "this",
        "these",
        "those",
        "with",
    ]
)
CUES = frozenset(
    ["no", "not", "never", "however", "despite", "failed", "lack", "without"]
)
WORDS = re.compile(r"\w+")


def tokens(text):
    return [w for w in WORDS.findall(text.lower()) if w not in STOP]


class BM25Index:
    def __init__(self, corpus):
        self.corpus = corpus
        counts = {}
        df = Counter()
        self.cues = {}
        lengths = {}
        for s in corpus.sources:
            words = tokens(s.title) * 2 + tokens(s.text)
            counts[s.source_id] = Counter(words)
            lengths[s.source_id] = len(words)
            df.update(counts[s.source_id].keys())
            self.cues[s.source_id] = len(CUES.intersection(counts[s.source_id]))
        n = len(counts)
        avg_len = sum(lengths.values()) / max(1, n) or 1
        self.idf = {w: math.log1p((n - f + 0.5) / (f + 0.5)) for w, f in df.items()}
        self.postings = defaultdict(list)
        for sid, freqs in counts.items():
            norm = 1.2 * (0.25 + 0.75 * lengths[sid] / avg_len)
            for w, f in freqs.items():
                self.postings[w].append((sid, self.idf[w] * f * 2.2 / (f + norm)))

    def search(self, claim, mode="relevance", limit=12):
        string(claim)
        integer(limit, 1, 12)
        if mode not in ("relevance", "challenge", "focus"):
            raise ValueError("unknown search mode")
        words = sorted(set(tokens(claim)).intersection(self.idf))
        if mode == "focus":
            words = sorted(words, key=lambda w: (-self.idf[w], w))[:4]
        scores = defaultdict(float)
        for word in words:
            for sid, weight in self.postings[word]:
                scores[sid] += weight
        if mode == "challenge":
            for sid in scores:
                scores[sid] *= 1 + 0.25 * min(3, self.cues[sid])
        ranking = sorted(scores, key=lambda sid: (-scores[sid], sid))[:limit]
        return [
            {
                "source_id": sid,
                "score": round(scores[sid] * 1000000),
                "snippet": self.corpus.by_id[sid].text[:180],
            }
            for sid in ranking
        ]

    def read(self, claim, source_id):
        terms = set(tokens(string(claim)))
        source = self.corpus.by_id[source_id]

        def weight(s):
            words = sorted(terms.intersection(tokens(source.text[s.start : s.end])))
            return sum(self.idf.get(w, 0) for w in words)

        top = sorted(source.sentences, key=weight, reverse=True)[:2]
        return tuple(
            Span(
                source_id,
                s.sentence_id,
                s.start,
                s.end,
                source.text[s.start : s.end],
                source.source_digest,
            )
            for s in top
        )
