"""Definition-only ranking and family-/report-clustered retrieval evaluation."""

import math
from collections import Counter, defaultdict

import numpy as np

from .cyber_data import normalize
from .data import source_from_lines
from .retrieval import BM25Index, tokens
from .schema import Corpus


def word_ranking(index, query):
    scores = defaultdict(float)
    for word in sorted(set(tokens(query)).intersection(index.idf)):
        for sid, weight in index.postings[word]:
            scores[sid] += weight
    return sorted(scores, key=lambda sid: (-scores[sid], sid))


def grams(text):
    text = normalize(text.lower())
    return Counter(text[i : i + 3] for i in range(len(text) - 2))


class CharacterIndex:
    def __init__(self, documents):
        counts = {sid: grams(text) for sid, text in documents.items()}
        frequency = Counter(g for row in counts.values() for g in row)
        self.idf = {
            g: 1 + math.log((1 + len(counts)) / (1 + n)) for g, n in frequency.items()
        }
        self.postings = defaultdict(list)
        for sid, features in counts.items():
            for g, value in self.vector(features).items():
                self.postings[g].append((sid, value))

    def vector(self, counts):
        vec = {
            g: (1 + math.log(n)) * self.idf[g]
            for g, n in counts.items()
            if g in self.idf
        }
        norm = math.sqrt(sum(w * w for w in vec.values()))
        return {g: w / norm for g, w in vec.items()}

    def rank(self, text):
        scores = defaultdict(float)
        for g, value in sorted(self.vector(grams(text)).items()):
            for sid, weight in self.postings[g]:
                scores[sid] += weight * value
        return sorted(scores, key=lambda sid: (-scores[sid], sid))


def fuse(rankings):
    scores = Counter()
    for ranking in rankings:
        for rank, sid in enumerate(ranking, 1):
            scores[sid] += 1 / (60 + rank)
    return sorted(scores, key=lambda sid: (-scores[sid], sid))


def attack_rankings(corpus, catalog):
    index = BM25Index(corpus)
    memberships = defaultdict(set)
    for entry in catalog["excerpts"].values():
        for rel in entry["relationships"]:
            memberships[rel["family_id"]].add(entry["source_id"])
    rows = []
    for q in catalog["queries"]:
        ranked = word_ranking(index, q["query"])
        valid = memberships[q["family_id"]]
        rows.append(
            {
                "query_id": q["query_id"],
                "gold": q["gold"],
                "cluster": q["family_id"],
                "ransomware_related": q["ransomware_related"],
                "rankings": {
                    "bm25": ranked[:5],
                    "family_bm25": [sid for sid in ranked if sid in valid][:5],
                },
            }
        )
    return rows


def tram_rankings(definitions, items):
    sources = [
        source_from_lines(tid, d["name"], [("definition", d["description"])])
        for tid, d in sorted(definitions.items())
    ]
    words = BM25Index(Corpus(tuple(sources)))
    chars = CharacterIndex({s.source_id: f"{s.title} {s.text}" for s in sources})
    rows = []
    for item in items:
        word, char = word_ranking(words, item["sentence"]), chars.rank(item["sentence"])
        rows.append(
            {
                "query_id": item["query_id"],
                "gold": item["gold"],
                "cluster": item["component"],
                "ransomware_related": item["ransomware_related"],
                "rankings": {
                    "bm25": word[:5],
                    "char_tfidf": char[:5],
                    "rrf": fuse([word, char])[:5],
                },
            }
        )
    return rows


def metrics(ranked, gold):
    hits = [int(sid in gold) for sid in ranked[:5]]
    ideal = sum(1 / math.log2(2 + i) for i in range(min(len(gold), 5)))
    return {
        "recall@5": sum(hits) / len(gold),
        "hit@1": float(bool(hits and hits[0])),
        "hit@5": float(any(hits)),
        "mrr@5": 1 / (1 + hits.index(1)) if any(hits) else 0.0,
        "ndcg@5": sum(hit / math.log2(2 + i) for i, hit in enumerate(hits)) / ideal,
    }


def paired_interval(groups, draws, seed):
    counts = np.array([len(groups[key]) for key in sorted(groups)])
    totals = np.array([sum(groups[key]) for key in sorted(groups)])
    rng = np.random.default_rng(seed)
    estimates = []
    for _ in range(draws):
        draw = rng.integers(0, len(counts), len(counts))
        estimates.append(totals[draw].sum() / counts[draw].sum())
    lo, hi = np.percentile(estimates, (2.5, 97.5))
    return {
        "difference": float(totals.sum() / counts.sum()),
        "ci95": [float(lo), float(hi)],
    }


def retrieval_summary(rows, contrast, p):
    result = {}
    for key, subset in (
        ("all", rows),
        ("ransomware_related", [q for q in rows if q["ransomware_related"]]),
    ):
        totals = defaultdict(Counter)
        groups = defaultdict(list)
        for q in subset:
            scores = {m: metrics(r, q["gold"]) for m, r in q["rankings"].items()}
            for method, values in scores.items():
                totals[method].update(values)
            groups[q["cluster"]].append(
                scores[contrast]["recall@5"] - scores["bm25"]["recall@5"]
            )
        result[key] = {
            "queries": len(subset),
            "clusters": len(groups),
            "largest_cluster": max(map(len, groups.values())),
            "metrics": {
                m: {k: v / len(subset) for k, v in values.items()}
                for m, values in totals.items()
            },
            "paired_recall_at_5": {
                "comparison": f"{contrast} minus bm25",
                **paired_interval(
                    groups, p["bootstrap"]["draws"], p["bootstrap"]["seed"]
                ),
            },
        }
    return result
