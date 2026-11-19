"""Classification, source dependence, and paired nonparametric uncertainty."""

from collections import defaultdict

import numpy as np

STATS = ("accuracy", "macro_f1", "coverage", "risk", "mean_reads", "mean_nli_pairs")


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def point(records, labels):
    confusion = [[0] * 5 for _ in labels]
    for row in records:
        confusion[labels.index(row["label"])][row["prediction"]] += 1
    n = len(records)
    if not n:
        raise ValueError("empty evaluation")
    per_class = {}
    f1s = []
    correct = covered = 0
    for i, label in enumerate(labels):
        cells = confusion[i]
        tp, total = cells[label], sum(cells)
        predicted = sum(r[label] for r in confusion)
        per_class[str(label)] = {
            "n": total,
            "recall": ratio(tp, total),
            "coverage": ratio(total - cells[4], total),
            "f1": ratio(2 * tp, total + predicted) or 0.0,
        }
        f1s.append(per_class[str(label)]["f1"])
        correct += tp
        covered += total - cells[4]

    def total(field):
        return sum(r[field] for r in records)

    return dict(
        n=n,
        accuracy=correct / n,
        macro_f1=sum(f1s) / len(labels),
        coverage=covered / n,
        risk=ratio(covered - correct, covered),
        confusion=confusion,
        per_class=per_class,
        pivotal_rate=total("pivotal") / n,
        sentence_recall=ratio(total("found_sentences"), total("gold_sentences")),
        complete_rationale_rate=ratio(total("complete"), total("eligible")),
        quote_integrity=ratio(total("intact"), total("checked")),
        **{
            "mean_" + key: total(key) / n
            for key in ("reads", "searches", "verifies", "nli_pairs")
        },
    )


def _draws(groups, labels, method, draws, seed):
    # A single weight matrix is reused for every configuration and paired contrast.
    names = sorted(groups)
    records = groups[names[0]]
    ids = [r["query_id"] for r in records]
    unit_key = "cluster_id" if method == "component" else "query_id"
    units = [r[unit_key] for r in records]
    unique = sorted(set(units))
    indices = {u: i for i, u in enumerate(unique)}
    inverse = [indices[u] for u in units]
    rng = np.random.default_rng(seed)
    weights = np.zeros((draws, len(unique)))
    for i in range(draws):
        weights[i] = np.bincount(
            rng.integers(len(unique), size=len(unique)), minlength=len(unique)
        )
    out = {}
    for name in names:
        rows = groups[name]
        if [r["query_id"] for r in rows] != ids or [r[unit_key] for r in rows] != units:
            raise ValueError("unpaired queries or bootstrap units")
        gold = np.array([r["label"] for r in rows])
        pred = np.array([r["prediction"] for r in rows])
        features = [
            np.ones(len(rows)),
            gold == pred,
            pred != 4,
            [r["reads"] for r in rows],
            [r["nli_pairs"] for r in rows],
        ]
        for k in labels:
            features.extend(
                [(gold == k) & (pred == k), (gold == k).astype(int) + (pred == k)]
            )
        unit_totals = np.zeros((len(unique), len(features)))
        np.add.at(unit_totals, inverse, np.asarray(features).T)
        totals = (weights @ unit_totals).T
        n, correct, covered, reads, pairs = totals[:5]
        zeros = np.zeros(draws)
        f1 = np.mean(
            [
                np.divide(2 * t, den, out=zeros.copy(), where=den != 0)
                for t, den in zip(totals[5::2], totals[6::2], strict=True)
            ],
            axis=0,
        )
        risk = np.divide(
            covered - correct, covered, out=np.full(draws, np.nan), where=covered != 0
        )
        out[name] = dict(
            zip(
                STATS,
                (correct / n, f1, covered / n, risk, reads / n, pairs / n),
                strict=True,
            )
        )
    return out


def _interval(samples):
    finite = samples[np.isfinite(samples)]
    low, high = np.quantile(finite, [0.025, 0.975]) if len(finite) else (None, None)
    return {"low": low, "high": high, "defined_draws": len(finite)}


def analyze(records, labels, draws=2000, seed=17, claim_sensitivity=False):
    groups = defaultdict(list)
    for row in records:
        groups[f"{row['policy']}-{row['budget']}"].append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: r["query_id"])
    configs = {
        name: {**point(rows, labels), "intervals": {}} for name, rows in groups.items()
    }
    contrasts = {}
    pairs = []
    for budget in (4, 8):
        for a, b in (
            ("challenge", "fixed"),
            ("guarded", "challenge"),
            ("first_hit", "fixed"),
        ):
            x, y = f"{a}-{budget}", f"{b}-{budget}"
            if x in configs and y in configs:
                pairs.append((x, y))
                differences = {
                    s: (
                        None
                        if None in (configs[x][s], configs[y][s])
                        else configs[x][s] - configs[y][s]
                    )
                    for s in STATS
                }
                contrasts[f"{x}_minus_{y}"] = {
                    "difference": differences,
                    "intervals": {},
                }
    for method in ("component", "claim") if claim_sensitivity else ("component",):
        sampled = _draws(groups, labels, method, draws, seed)
        for name, stats in sampled.items():
            configs[name]["intervals"][method] = {
                s: _interval(v) for s, v in stats.items()
            }
        for x, y in pairs:
            contrasts[f"{x}_minus_{y}"]["intervals"][method] = {
                s: _interval(sampled[x][s] - sampled[y][s]) for s in STATS
            }
    return {"configs": configs, "contrasts": contrasts}


def summarize(records, labels, draws=2000, seed=17):
    """Summarize one policy/budget with component-level intervals."""
    rows = [dict(r, policy="single", budget=1) for r in records]
    return analyze(rows, list(labels), draws, seed)["configs"]["single-1"]
