"""Audit published artifacts and independently recompute decisions and uncertainty."""

import json
import math
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import numpy as np

from .data import file_digest, load_dataset, pin_path, read_corpus
from .experiment import CLASSES, csv_bytes, demo_claim, make_index, protocol_path, read
from .report import validate_run
from .schema import digest, integer, keys

# Hashes of [original query ID, numeric benchmark class, held-out cluster ID],
# sorted by query ID. Calibration lacks the final field. No model output used.
IDENTITIES = {
    "calibration": "a7253c9caff25daf626b43f4a8138ef1865c9f48565259724c64488dd5ff512a",
    "scifact": "7b583b9e778bf6c2f8ac156852a621827ddf62675340337117e7e5371c2629ce",
    "climate": "2d1f09087e08553a4144aacda4804b3003f454f4b7c254866b6f7a50db6e4442",
}
BOOT = ("accuracy", "macro_f1", "coverage", "risk", "mean_reads", "mean_nli_pairs")
RECORD_FIELDS = [
    "query_id",
    "cluster_id",
    "run_id",
    "label",
    "policy",
    "budget",
    "prediction",
    "base",
    "scores",
    "withdrawals",
    "pivotal",
    "searches",
    "reads",
    "verifies",
    "successes",
    "nli_pairs",
    "checked",
    "intact",
    "gold_sentences",
    "found_sentences",
    "eligible",
    "complete",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compare(actual, expected, location="summary"):
    if isinstance(expected, dict):
        require(
            type(actual) is dict and actual.keys() == expected.keys(),
            f"fields differ at {location}",
        )
        for k, v in expected.items():
            compare(actual[k], v, f"{location}/{k}")
    elif isinstance(expected, list):
        require(
            type(actual) is list and len(actual) == len(expected),
            f"shape differs at {location}",
        )
        for x, y in zip(actual, expected, strict=True):
            compare(x, y, location)
    elif isinstance(expected, (float, np.floating)):
        require(
            type(actual) in (int, float, np.float64)
            and math.isfinite(actual)
            and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12),
            f"numeric mismatch at {location}",
        )
    else:
        require(
            type(actual) is type(expected) and actual == expected,
            f"value differs at {location}",
        )


def decision(scores, threshold, omit=None):
    plus = {s[0] for s in scores if s[0] != omit and s[4] >= threshold}
    minus = {s[0] for s in scores if s[0] != omit and s[3] >= threshold}
    if plus and minus:
        return 3 if len(plus | minus) > 1 else 2
    return 0 if plus else 1 if minus else 2


def _scores(scores, nsources):
    require(type(scores) is list and len(scores) <= 16, "invalid score array")
    seen = set()
    for s in scores:
        require(type(s) is list and len(s) == 6, "invalid score row")
        sid, j, n, c, e, z = s
        integer(sid, 0, nsources - 1)
        integer(j, 0)
        integer(n, 1)
        for value in (c, e, z):
            integer(value, 0, 1000000)
        require(
            c + e + z == 1000000 and (sid, j) not in seen,
            "invalid score mass or duplicated span",
        )
        seen.add((sid, j))


def check_calibration(cal, p, *, prepared_dir=None):
    keys(
        cal,
        (
            "schema_version",
            "protocol_sha256",
            "split_sha256",
            "corpus_digest",
            "model",
            "model_digest",
            "grid",
            "macro_f1",
            "threshold",
            "queries",
        ),
    )
    info = p["datasets"]["scifact"]
    require(
        cal["protocol_sha256"] == file_digest(protocol_path())
        and cal["schema_version"] == 1,
        "calibration protocol mismatch",
    )
    require(
        cal["split_sha256"] == info["splits"]["calibration"]["sha256"]
        and cal["corpus_digest"] == info["corpus_digest"],
        "calibration source mismatch",
    )
    lock = read(pin_path("model-lock.json"))
    require(
        cal["model"]["lock"] == lock
        and digest(cal["model"]) == cal["model_digest"]
        and file_digest(pin_path("model-lock.json")) == p["model"]["lock_sha256"],
        "model identity mismatch",
    )
    meta = cal["model"]
    keys(
        meta,
        (
            "lock",
            "encoding",
            "max_tokens",
            "labels",
            "quantization",
            "dtype",
            "device",
            "batch_size",
            "versions",
        ),
    )
    require(
        meta["labels"] == ["contradiction", "entailment", "neutral"]
        and meta["max_tokens"] == 512
        and meta["dtype"] == "float32"
        and meta["device"] in ("cpu", "cuda", "mps")
        and meta["encoding"] == "pair-premise-first/right-only-first/offset-prefix-v1"
        and meta["quantization"] == "largest-remainder-millionths-v1",
        "wrong model encoding",
    )
    integer(meta["batch_size"], 1)
    keys(meta["versions"], ("torch", "transformers", "tokenizers", "safetensors"))
    grid = p["calibration"]["grid"]
    require(
        cal["grid"] == grid
        and len(cal["queries"]) == info["splits"]["calibration"]["queries"],
        "calibration grid or denominator mismatch",
    )
    ids = []
    matrices = np.zeros((len(grid), 3, 5), dtype=int)
    for row in cal["queries"]:
        keys(row, ("query_id", "label", "scores", "predictions"))
        _scores(row["scores"], info["sources"])
        y = integer(row["label"], 0, 2)
        ids.append([row["query_id"], y])
        predictions = [decision(row["scores"], t) for t in grid]
        require(
            predictions == row["predictions"],
            "calibration decisions differ from scores",
        )
        for i, predicted in enumerate(predictions):
            matrices[i, y, predicted] += 1
    require(
        digest(sorted(ids)) == IDENTITIES["calibration"],
        "calibration identities differ",
    )
    vals = []
    for matrix in matrices:
        value = Fraction()
        for k in range(3):
            den = int(matrix[k].sum() + matrix[:, k].sum())
            if den:
                value += Fraction(2 * int(matrix[k, k]), den)
        vals.append(value / 3)
    compare(cal["macro_f1"], list(map(float, vals)), "calibration macro-F1")
    require(
        cal["threshold"] == max(zip(vals, grid, strict=True))[1],
        "calibrated threshold differs",
    )

    if prepared_dir is not None:
        _replay_calibration(cal, p, prepared_dir)


def _replay_calibration(cal, p, prepared):
    corpus, queries = load_dataset(prepared, "scifact", "calibration")
    require(
        corpus.digest == cal["corpus_digest"]
        and file_digest(Path(prepared) / "scifact/calibration.jsonl")
        == cal["split_sha256"],
        "prepared calibration source differs from frozen split",
    )
    index = make_index(corpus)
    ordinals = {
        s.source_id: (i, {t.sentence_id: j for j, t in enumerate(s.sentences)})
        for i, s in enumerate(corpus.sources)
    }
    ledger = {row["query_id"]: row for row in cal["queries"]}
    for q in queries:
        row = ledger[q["query_id"]]
        require(
            row["label"] == CLASSES.index(q["label"]),
            "prepared calibration label mismatch",
        )
        expected = []
        for hit in index.search(q["claim"])[: p["calibration"]["budget"]]:
            sid = hit["source_id"]
            s, positions = ordinals[sid]
            for span in index.read(q["claim"], sid):
                expected.append((s, positions[span.sentence_id], span))
        require(
            len(expected) == len(row["scores"]),
            "calibration fixed-eight pair count mismatch",
        )
        for (s, j, span), score in zip(expected, row["scores"], strict=True):
            require(
                score[:2] == [s, j]
                and 0 < score[2] <= len(span.quote)
                and bool(span.quote[: score[2]].strip()),
                "calibration fixed-eight acquisition or retained prefix mismatch",
            )


def check_records(records, name, cal, p):
    info = p["datasets"][name]
    grouped = defaultdict(dict)
    seen_scores = {}
    for r in records:
        keys(r, RECORD_FIELDS)
        qid, policy, budget = r["query_id"], r["policy"], r["budget"]
        require(
            type(qid) is str and qid.isascii() and qid.isdecimal(), "invalid query ID"
        )
        require(
            policy in p["policies"] and type(budget) is int and budget in p["budgets"],
            "invalid configuration",
        )
        expected_id = digest(
            [
                1,
                name,
                qid,
                policy,
                budget,
                cal["threshold"],
                cal["model_digest"],
                info["corpus_digest"],
            ]
        )
        require(
            r["run_id"] == expected_id and (policy, budget) not in grouped[qid],
            "duplicate run or mismatched identity",
        )
        require(
            type(r["label"]) is int and r["label"] in p["gold_classes"][name],
            "invalid gold class",
        )
        grouped[qid][policy, budget] = r
        _scores(r["scores"], info["sources"])
        for s in r["scores"]:
            key = (qid, s[0], s[1])
            require(
                seen_scores.setdefault(key, s) == s,
                "same query and span has conflicting scores",
            )
        sids = {s[0] for s in r["scores"]}
        base = decision(r["scores"], cal["threshold"])
        drops = sorted([i, decision(r["scores"], cal["threshold"], i)] for i in sids)
        pivotal = int(any(pred != base for _, pred in drops))
        pred = 4 if policy == "guarded" and pivotal else base
        compare(
            [r["base"], r["withdrawals"], r["pivotal"], r["prediction"]],
            [base, drops, pivotal, pred],
            "source withdrawal",
        )
        for k in (
            "searches",
            "reads",
            "verifies",
            "nli_pairs",
            "checked",
            "intact",
            "found_sentences",
            "gold_sentences",
            "eligible",
            "complete",
        ):
            integer(r[k], 0)
        succ = r["successes"]
        keys(succ, ("search", "read", "verify"))
        for k, attempt_key in (
            ("search", "searches"),
            ("read", "reads"),
            ("verify", "verifies"),
        ):
            integer(succ[k], 0)
            require(
                succ[k] <= r[attempt_key] <= 3 * succ[k],
                "attempt count exceeds retry bounds",
            )
        require(
            1 <= succ["search"] <= 2
            and len(sids) == succ["read"] == succ["verify"] <= budget
            and r["nli_pairs"] == r["checked"] == r["intact"] == len(r["scores"]),
            "logical cost or quote count mismatch",
        )
        require(
            r["found_sentences"] <= r["gold_sentences"]
            and r["eligible"] == int(r["gold_sentences"] > 0)
            and r["complete"] <= r["eligible"],
            "invalid evidence observations",
        )
    target = {(pol, b) for pol in p["policies"] for b in p["budgets"]}
    ids = []
    for qid, runs in grouped.items():
        require(runs.keys() == target, "incomplete configuration coverage")
        ref = runs["fixed", 8]
        for r in runs.values():
            require(
                (r["label"], r["cluster_id"], r["gold_sentences"])
                == (ref["label"], ref["cluster_id"], ref["gold_sentences"]),
                "query metadata drift",
            )
        for b in p["budgets"]:
            require(
                runs["guarded", b]["scores"] == runs["challenge", b]["scores"],
                "guarded acquisition drift",
            )
        ids.append([qid, ref["label"], ref["cluster_id"]])
    require(
        digest(sorted(ids)) == IDENTITIES[name], "query identities or clusters differ"
    )
    return grouped


def _divide(a, b):
    return a / b if b else None


def _independent_point(rows, labels):
    cm = np.zeros((len(labels), 5), dtype=int)
    for r in rows:
        cm[r["label"], r["prediction"]] += 1
    correct = int(cm.trace())
    n = int(cm.sum())
    covered = n - int(cm[:, 4].sum())
    per = {}
    for k in labels:
        t, den = int(cm[k, k]), int(cm[k].sum())
        per[str(k)] = {
            "n": den,
            "recall": _divide(t, den),
            "coverage": _divide(den - int(cm[k, 4]), den),
            "f1": _divide(2 * t, den + int(cm[:, k].sum())) or 0.0,
        }

    def total(field):
        return sum(r[field] for r in rows)

    return dict(
        n=n,
        accuracy=correct / n,
        macro_f1=sum(v["f1"] for v in per.values()) / len(labels),
        coverage=covered / n,
        risk=_divide(covered - correct, covered),
        per_class=per,
        confusion=cm.tolist(),
        pivotal_rate=total("pivotal") / n,
        sentence_recall=_divide(total("found_sentences"), total("gold_sentences")),
        complete_rationale_rate=_divide(total("complete"), total("eligible")),
        quote_integrity=_divide(total("intact"), total("checked")),
        **{
            "mean_" + k: total(k) / n
            for k in ("reads", "verifies", "searches", "nli_pairs")
        },
    )


def _interval(samples):
    finite = np.sort(samples[np.isfinite(samples)])

    def percentile(p):
        if not len(finite):
            return None
        i = p * (len(finite) - 1)
        left = math.floor(i)
        return float(finite[left] + (i - left) * (finite[math.ceil(i)] - finite[left]))

    return {
        "low": percentile(0.025),
        "high": percentile(0.975),
        "defined_draws": len(finite),
    }


def _independent_samples(groups, labels, method, draws, seed):
    # Sum whole sampled unit confusion matrices directly. One row-index sample
    # feeds all configurations, including cases with a missing reference class.
    field = "query_id" if method == "claim" else "cluster_id"
    unique = sorted({r[field] for r in next(iter(groups.values()))})
    mapping = {k: i for i, k in enumerate(unique)}
    rng = np.random.default_rng(seed)
    sampled = rng.integers(len(unique), size=(draws, len(unique)))
    out = {}
    for name, rows in groups.items():
        units = np.zeros((len(unique), len(labels) * 5 + 2), dtype=np.int64)
        for r in rows:
            u = units[mapping[r[field]]]
            u[r["label"] * 5 + r["prediction"]] += 1
            u[-2:] += (r["reads"], r["nli_pairs"])
        gathered = np.concatenate(
            [
                units[chunk].sum(axis=1)
                for chunk in np.array_split(sampled, max(1, math.ceil(draws / 32)))
            ]
        )
        matrices = gathered[:, :-2].reshape(draws, len(labels), 5)
        n = matrices.sum(axis=(1, 2))
        diagonal = matrices.diagonal(axis1=1, axis2=2)
        correct = diagonal.sum(axis=1)
        covered = n - matrices[:, :, 4].sum(axis=1)
        denominators = matrices.sum(axis=2) + matrices.sum(axis=1)[:, : len(labels)]
        f1 = np.divide(
            2 * diagonal,
            denominators,
            out=np.zeros(diagonal.shape),
            where=denominators != 0,
        )
        risk = np.divide(
            covered - correct, covered, out=np.full(draws, np.nan), where=covered != 0
        )
        out[name] = dict(
            zip(
                BOOT,
                (
                    correct / n,
                    f1.mean(axis=1),
                    covered / n,
                    risk,
                    gathered[:, -2] / n,
                    gathered[:, -1] / n,
                ),
                strict=True,
            )
        )
    return out


def check_statistics(summary, records, labels, draws, seed, claim_sensitivity):
    groups = defaultdict(list)
    for r in records:
        groups[f"{r['policy']}-{r['budget']}"].append(r)
    points = {
        k: {**_independent_point(v, labels), "intervals": {}} for k, v in groups.items()
    }
    deltas = {}
    for method in ("component", "claim") if claim_sensitivity else ("component",):
        samples = _independent_samples(groups, labels, method, draws, seed)
        for k, data in samples.items():
            points[k]["intervals"][method] = {m: _interval(v) for m, v in data.items()}
        for budget in (4, 8):
            for a, b in (
                ("challenge", "fixed"),
                ("guarded", "challenge"),
                ("first_hit", "fixed"),
            ):
                x, y = f"{a}-{budget}", f"{b}-{budget}"
                key = f"{x}_minus_{y}"
                entry = deltas.setdefault(key, {"difference": {}, "intervals": {}})
                for m in BOOT:
                    left, right = points[x][m], points[y][m]
                    entry["difference"][m] = (
                        None if None in (left, right) else left - right
                    )
                entry["intervals"][method] = {
                    m: _interval(samples[x][m] - samples[y][m]) for m in BOOT
                }
    compare(summary["configs"], points, "configurations")
    compare(summary["contrasts"], deltas, "paired contrasts")


def _review_evidence(record, corpus, query):
    kept = set()
    for sid, j, length, *_ in record["scores"]:
        source = corpus.sources[sid]
        require(j < len(source.sentences), "unknown sentence ordinal")
        sentence = source.sentences[j]
        end = sentence.start + length
        require(end <= sentence.end, "retained score extends past sentence")
        if not source.text[end : sentence.end].strip():
            kept.add((source.source_id, sentence.sentence_id))
    groups = [
        {(e["source_id"], j) for j in e["sentence_ids"]}
        for e in query["evidence"]
        if e["label"] != "INSUFFICIENT"
    ]
    union = set().union(*groups)
    require(
        record["label"] == CLASSES.index(query["label"])
        and record["cluster_id"] == query["cluster_id"]
        and record["gold_sentences"] == len(union)
        and record["found_sentences"] == len(union & kept)
        and record["complete"] == int(any(not (g - kept) for g in groups)),
        "annotation observation mismatch",
    )


def _trace_record(run, r, q, corpus, index, ordinals, cal):
    require(
        run["run_id"] == r["run_id"]
        and run["claim"] == q["claim"]
        and run["status"] == "complete"
        and run["config"]
        == {
            "policy": r["policy"],
            "budget": r["budget"],
            "threshold": cal["threshold"],
            "model_digest": cal["model_digest"],
        },
        "trace binding mismatch",
    )
    validate_run(run, corpus, index=index)
    actual = []
    for v in run["scores"]:
        s = v["span"]
        i, ordmap = ordinals[s["source_id"]]
        actual.append(
            [
                i,
                ordmap[s["sentence_id"]],
                s["end"] - s["start"],
                v["contradiction"],
                v["entailment"],
                v["neutral"],
            ]
        )
    assessment = run["assessment"]
    require(
        CLASSES.index(assessment["final"]["verdict"]) == r["prediction"]
        and CLASSES.index(assessment["base"]["verdict"]) == r["base"]
        and sorted(
            [ordinals[sid][0], CLASSES.index(d["verdict"])]
            for sid, d in assessment["withdrawals"].items()
        )
        == r["withdrawals"],
        "trace decision disagrees with numeric decision",
    )
    attempts = run["usage"]["attempts"]
    require(
        actual == r["scores"]
        and run["usage"]["successes"] == r["successes"]
        and (attempts["search"], attempts["read"], attempts["verify"])
        == (r["searches"], r["reads"], r["verifies"]),
        "public record differs from trace",
    )


def _climate_replay(r, corpus, index, q, ordinals, threshold):
    """Replay lexical acquisition from original local data and stored numeric scores."""
    claim = q["claim"]
    initial = [h["source_id"] for h in index.search(claim)]
    ranked = list(initial)
    by_source = defaultdict(list)
    for s in r["scores"]:
        by_source[s[0]].append(s)
    seen = set()
    retained = []
    searches = 1
    while len(seen) < r["budget"]:
        verdict = decision(retained, threshold)
        if r["policy"] == "first_hit" and verdict != 2:
            break
        if (
            r["policy"] in ("guarded", "challenge")
            and searches == 1
            and (len(seen) >= 3 or not ranked)
        ):
            next_rank = [
                h["source_id"]
                for h in index.search(claim, "focus" if verdict == 2 else "challenge")
            ]
            ranked = [
                sid for sid in dict.fromkeys(next_rank + initial) if sid not in seen
            ]
            searches += 1
        if not ranked:
            break
        sid = ranked.pop(0)
        seen.add(sid)
        src, positions = ordinals[sid]
        expected = [positions[s.sentence_id] for s in index.read(claim, sid)]
        observed = by_source[src]
        require(
            expected == [s[1] for s in observed],
            "local Climate sentence retrieval mismatch",
        )
        retained.extend(observed)
    require(
        retained == r["scores"]
        and searches == r["successes"]["search"]
        and len(seen) == r["successes"]["read"],
        "local Climate policy replay mismatch",
    )


def _official_manifest(path):
    m = read(path / "manifest.json")
    keys(
        m,
        (
            "schema_version",
            "files",
            "site_files",
            "protocol_sha256",
            "calibration_sha256",
        ),
    )
    require(
        m["schema_version"] == 1
        and m["protocol_sha256"] == file_digest(protocol_path())
        and file_digest(path / "protocol.json") == m["protocol_sha256"]
        and file_digest(path / "calibration.json") == m["calibration_sha256"],
        "release protocol/calibration pin mismatch",
    )
    found = {
        str(f.relative_to(path)): file_digest(f)
        for f in path.rglob("*")
        if f.is_file() and f != path / "manifest.json"
    }
    require(found == m["files"], "public artifact inventory or checksum mismatch")
    return m


def verify_results(
    path,
    prepared_dir=None,
    *,
    site_dir=None,
    frozen_calibration=None,
    frozen_sha256=None,
):
    path = Path(path)
    manifest = _official_manifest(path)
    if frozen_calibration is not None and frozen_sha256 is not None:
        raise ValueError("supply one external calibration reference")
    if frozen_sha256 is None:
        reference = (
            Path(frozen_calibration)
            if frozen_calibration is not None
            else protocol_path().with_name("calibration.json")
        )
        if not reference.is_file():
            raise ValueError(
                "missing frozen calibration reference; pass --frozen-calibration or --frozen-sha256"
            )
        require(
            not reference.samefile(path / "calibration.json"),
            "frozen calibration reference must be separate from the release copy",
        )
        frozen_sha256 = file_digest(reference)
    require(
        manifest["calibration_sha256"] == frozen_sha256,
        "release differs from independently frozen calibration",
    )
    files = manifest["files"]
    p = read(protocol_path())
    cal = read(path / "calibration.json")
    check_calibration(cal, p, prepared_dir=prepared_dir)
    cost = read(path / "physical.json")
    keys(cost, ("schema_version", "invocation", "sessions"))
    integer(cost["schema_version"], 1, 1)
    for field in ("invocation", "sessions"):
        keys(cost[field], p["evaluation"]["splits"])
    for name in cost["sessions"]:
        entries = cost["sessions"][name]
        require(
            type(entries) is list and cost["invocation"][name] in entries,
            "missing physical invocation record",
        )
        for entry in entries:
            keys(
                entry,
                (
                    "device",
                    "batch_size",
                    "elapsed_seconds",
                    "requested_pairs",
                    "physical_pairs",
                    "truncated_physical_pairs",
                    "cache_reused_pairs",
                ),
            )
            require(
                entry["device"] == cal["model"]["device"]
                and entry["batch_size"] == cal["model"]["batch_size"],
                "physical stage model mismatch",
            )
            for key in (
                "requested_pairs",
                "physical_pairs",
                "truncated_physical_pairs",
                "cache_reused_pairs",
            ):
                integer(entry[key], 0)
            require(
                entry["requested_pairs"]
                == entry["physical_pairs"] + entry["cache_reused_pairs"]
                and entry["truncated_physical_pairs"] <= entry["physical_pairs"]
                and type(entry["elapsed_seconds"]) in (int, float)
                and math.isfinite(entry["elapsed_seconds"])
                and entry["elapsed_seconds"] >= 0,
                "invalid physical cost",
            )
    summary = read(path / "summary.json")
    keys(summary, ("schema_version", "threshold", "model_digest", "datasets"))
    require(
        summary["schema_version"] == 1
        and summary["threshold"] == cal["threshold"]
        and summary["model_digest"] == cal["model_digest"]
        and summary["datasets"].keys() == p["gold_classes"].keys(),
        "summary provenance mismatch",
    )
    require(
        (path / "summary.csv").read_bytes() == csv_bytes(summary),
        "CSV differs from summary",
    )
    expected_files = {
        "physical.json",
        "protocol.json",
        "calibration.json",
        "summary.json",
        "summary.csv",
    }
    counts = {}
    for name, split in p["evaluation"]["splits"].items():
        ds = path / name
        info = p["datasets"][name]
        data = read(ds / "records.json.gz")
        keys(data, ("schema_version", "records"))
        integer(data["schema_version"], 1, 1)
        rows = data["records"]
        runs = check_records(rows, name, cal, p)
        counts[name] = len(rows)
        expected_files.add(f"{name}/records.json.gz")
        report = summary["datasets"][name]
        keys(report, ("configs", "contrasts", "queries", "clusters", "largest_cluster"))
        for k in ("queries", "clusters", "largest_cluster"):
            require(report[k] == info["splits"][split][k], "split size mismatch")
        unc = p["evaluation"]["uncertainty"]
        check_statistics(
            report,
            rows,
            p["gold_classes"][name],
            unc["draws"],
            unc["seed"],
            name == "climate",
        )
        if name == "scifact":
            require(
                file_digest(ds / "dev.jsonl") == info["splits"][split]["sha256"]
                and file_digest(ds / "corpus.jsonl") == info["corpus_sha256"],
                "public SciFact data pin mismatch",
            )
            public_corpus = read_corpus(ds / "corpus.jsonl")
            with (ds / "dev.jsonl").open() as stream:
                public_queries = [json.loads(line) for line in stream]
            expected_files.update(("scifact/corpus.jsonl", "scifact/dev.jsonl"))
        if prepared_dir:
            corpus, qs = load_dataset(prepared_dir, name, split)
            require(
                file_digest(Path(prepared_dir) / name / (split + ".jsonl"))
                == info["splits"][split]["sha256"],
                "local split pin mismatch",
            )
        elif name == "scifact":
            corpus, qs = public_corpus, public_queries
        else:
            continue
        require(
            corpus.digest == info["corpus_digest"], "benchmark corpus identity mismatch"
        )
        index = make_index(corpus)
        ordinals = {
            s.source_id: (i, {t.sentence_id: j for j, t in enumerate(s.sentences)})
            for i, s in enumerate(corpus.sources)
        }
        for q in qs:
            qid = q["query_id"]
            if name == "scifact":
                rel = f"scifact/traces/{qid}.json.gz"
                expected_files.add(rel)
                traces = read(path / rel)
                keys(traces, ("schema_version", "query_id", "runs"))
                require(
                    traces["schema_version"] == 1
                    and traces["query_id"] == qid
                    and traces["runs"].keys() == {f"{pol}-{b}" for pol, b in runs[qid]},
                    "trace group mismatch",
                )
            for (pol, b), r in runs[qid].items():
                _review_evidence(r, corpus, q)
                if name == "scifact":
                    _trace_record(
                        traces["runs"][f"{pol}-{b}"], r, q, corpus, index, ordinals, cal
                    )
                else:
                    _climate_replay(r, corpus, index, q, ordinals, cal["threshold"])
    require(set(files) == expected_files, "unexpected or missing public results files")
    import hashlib

    ids = sorted(
        (q["query_id"] for q in public_queries),
        key=lambda s: hashlib.sha256(s.encode()).digest(),
    )[:40]
    allowed = {"index.json", "summary.json"} | {f"claims/{qid}.json" for qid in ids}
    require(
        manifest["site_files"].keys() == allowed, "site manifest inventory mismatch"
    )
    if site_dir is not None:
        base = Path(site_dir)
        require(
            manifest["site_files"] == {k: file_digest(base / k) for k in allowed},
            "site file digest mismatch",
        )
        _verify_site(path, Path(site_dir), public_corpus, public_queries, cal, p)
    return {
        "schema_version": 1,
        "verified": True,
        "runs": counts,
        "climate_source_replay": bool(prepared_dir),
        "site_verified": site_dir is not None,
    }


def _verify_site(out, site, corpus, queries, cal, p):
    import hashlib

    selected = sorted(
        queries, key=lambda q: hashlib.sha256(q["query_id"].encode()).digest()
    )[:40]
    choices = [
        {
            "query_id": q["query_id"],
            "claim": q["claim"],
            "path": f"claims/{q['query_id']}.json",
        }
        for q in selected
    ]
    expected = {
        "schema_version": 1,
        "claims": choices,
        "policies": p["policies"],
        "budgets": p["budgets"],
        "threshold": cal["threshold"],
        "model_digest": cal["model_digest"],
        "corpus_digest": corpus.digest,
    }
    compare(read(site / "index.json"), expected, "site index")
    require(
        file_digest(site / "summary.json") == file_digest(out / "summary.json"),
        "site summary mismatch",
    )
    allowed = {"index.json", "summary.json"} | {c["path"] for c in choices}
    require(
        {str(f.relative_to(site)) for f in site.rglob("*") if f.is_file()} == allowed,
        "unexpected site data files",
    )
    for q, choice in zip(selected, choices, strict=True):
        group = read(out / f"scifact/traces/{q['query_id']}.json.gz")
        compare(
            read(site / choice["path"]),
            demo_claim(q, group["runs"], corpus),
            "site trace/source",
        )
