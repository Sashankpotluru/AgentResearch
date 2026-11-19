"""Frozen calibration and held-out evaluation. Only claim text enters the investigator."""

import csv
import gzip
import hashlib
import io
import json
import sysconfig
import time
from contextlib import contextmanager
from fractions import Fraction
from functools import lru_cache
from itertools import batched
from pathlib import Path
from tempfile import NamedTemporaryFile

from .data import file_digest, load_dataset, pin_path
from .engine import Investigator
from .metrics import analyze
from .retrieval import BM25Index
from .schema import RunConfig, digest
from .store import RunStore
from .tools import CorpusTools

CLASSES = ("SUPPORTS", "REFUTES", "INSUFFICIENT", "CONFLICT", "ABSTAIN")


def protocol_path():
    path = Path(__file__).resolve().parents[2] / "experiments/protocol.json"
    return (
        path
        if path.is_file()
        else Path(sysconfig.get_path("data")) / "share/agentresearch/protocol.json"
    )


def read(path):
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)

    def unique(pairs):
        obj = dict(pairs)
        if len(obj) != len(pairs):
            raise ValueError("duplicate artifact key")
        return obj

    def invalid(value):
        raise ValueError(f"nonfinite artifact value {value}")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def encoded(obj):
    return (
        json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        + b"\n"
    )


def write_bytes(path, data, *, staging=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=staging or path.parent, delete=False) as tmp:
        try:
            tmp.write(data)
            tmp.close()
            Path(tmp.name).replace(path)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


def write(path, obj, *, staging=None):
    data = encoded(obj)
    write_bytes(
        path,
        gzip.compress(data, compresslevel=6, mtime=0)
        if Path(path).suffix == ".gz"
        else data,
        staging=staging,
    )


def _public_staging(destination, work, public_roots):
    # A killed writer may leave an unrenamed file here. Never enumerate staging
    # as part of either public release inventory or remove someone else's file.
    destination.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    if work.stat().st_dev == destination.stat().st_dev:
        stage = work / ".pending"
    else:
        stage = destination.parent / ".agentresearch-pending"
    if any(stage.resolve().is_relative_to(root.resolve()) for root in public_roots):
        raise ValueError("public outputs require separate private staging")
    stage.mkdir(exist_ok=True)
    if stage.stat().st_dev != destination.stat().st_dev:
        raise ValueError(
            "provide a work directory on the output filesystem for atomic publication"
        )
    return stage


def source_ordinals(corpus):
    return {
        s.source_id: (i, {t.sentence_id: j for j, t in enumerate(s.sentences)})
        for i, s in enumerate(corpus.sources)
    }


def observation(query, run, corpus, ordinals):
    """Join retained evidence to annotations after all tool calls have finished."""
    if run["status"] != "complete":
        raise ValueError(f"incomplete run {run['run_id']}")
    kept, numeric, intact = set(), [], 0
    for score in run["scores"]:
        span = score["span"]
        sid, sentence_id = span["source_id"], span["sentence_id"]
        source = corpus.by_id[sid]
        sentence = source.by_sentence[sentence_id]
        exact = (
            sentence.start == span["start"] < span["end"] <= sentence.end
            and source.source_digest == span["source_digest"]
            and source.text[span["start"] : span["end"]] == span["quote"]
        )
        intact += int(exact)
        if exact and not source.text[span["end"] : sentence.end].strip():
            kept.add((sid, sentence_id))
        idx, sentences = ordinals[sid]
        numeric.append(
            [
                idx,
                sentences[sentence_id],
                span["end"] - span["start"],
                score["contradiction"],
                score["entailment"],
                score["neutral"],
            ]
        )
    rationales = [
        {(e["source_id"], s) for s in e["sentence_ids"]}
        for e in query["evidence"]
        if e["label"] != "INSUFFICIENT"
    ]
    gold = set().union(*rationales)
    a, usage = run["assessment"], run["usage"]
    return {
        "query_id": query["query_id"],
        "cluster_id": query["cluster_id"],
        "run_id": run["run_id"],
        "label": CLASSES.index(query["label"]),
        "policy": run["config"]["policy"],
        "budget": run["config"]["budget"],
        "prediction": CLASSES.index(a["final"]["verdict"]),
        "base": CLASSES.index(a["base"]["verdict"]),
        "scores": numeric,
        "successes": usage["successes"],
        "withdrawals": sorted(
            [ordinals[k][0], CLASSES.index(v["verdict"])]
            for k, v in a["withdrawals"].items()
        ),
        "pivotal": int(bool(a["pivotal_sources"])),
        "searches": usage["attempts"]["search"],
        "reads": usage["attempts"]["read"],
        "verifies": usage["attempts"]["verify"],
        "nli_pairs": usage["nli_pairs"],
        "checked": len(numeric),
        "intact": intact,
        "gold_sentences": len(gold),
        "found_sentences": len(kept & gold),
        "eligible": int(bool(gold)),
        "complete": int(any(group <= kept for group in rationales)),
    }


def demo_claim(query, runs, corpus):
    ids = {
        h["source_id"]
        for run in runs.values()
        for e in run["events"]
        if e["tool"] == "search" and e["error"] is None
        for h in e["output"]
    }
    return dict(
        schema_version=1,
        **{k: query[k] for k in ("query_id", "claim", "label", "cluster_id")},
        runs=runs,
        sources={k: corpus.by_id[k].to_dict() for k in sorted(ids)},
    )


def checkpoint_id(dataset, query_id, policy, budget, threshold, model, corpus):
    return digest([1, dataset, query_id, policy, budget, threshold, model, corpus])


def make_index(corpus):
    index = BM25Index(corpus)
    index.search = lru_cache(maxsize=128)(index.search)
    index.read = lru_cache(maxsize=512)(index.read)
    return index


def prefetch(claims, index, verifier, *, calibration=False, threshold=None):
    """Warm fixed top eight, then only new reads in the selected challenge branch."""
    from .decision import decide

    if not calibration and threshold is None:
        raise ValueError("held-out prefetch requires a frozen threshold")

    count = 0
    for group in batched(claims, 16):
        rankings = [
            [hit["source_id"] for hit in index.search(claim)] for claim in group
        ]
        pairs, prefixes = [], []
        for claim, ranking in zip(group, rankings, strict=True):
            start = len(pairs)
            for sid in ranking[:3]:
                pairs.extend((claim, span) for span in index.read(claim, sid))
            prefixes.append(slice(start, len(pairs)))
            for sid in ranking[3:8]:
                pairs.extend((claim, span) for span in index.read(claim, sid))
        initial = verifier.verify_many(pairs)
        count += len(initial)
        if calibration:
            continue
        additional = []
        for claim, ranking, prefix in zip(group, rankings, prefixes, strict=True):
            verdict = decide(initial[prefix], threshold).verdict
            mode = "focus" if verdict == "INSUFFICIENT" else "challenge"
            secondary = [hit["source_id"] for hit in index.search(claim, mode)]
            branch = list(dict.fromkeys(ranking[:3] + secondary + ranking))[:8]
            for sid in branch:
                if sid not in ranking[:8]:
                    additional.extend((claim, span) for span in index.read(claim, sid))
        count += len(verifier.verify_many(additional))
    return count


class CountedCache:
    def __init__(self, cache):
        self.cache = cache
        self.model_digest = cache.model_digest
        self.requested = 0

    def verify_many(self, pairs):
        pairs = tuple(pairs)
        self.requested += len(pairs)
        return self.cache.verify_many(pairs)

    def verify(self, claim, spans):
        return self.verify_many((claim, s) for s in spans)


@contextmanager
def runtime(corpus, model, cache, device, batch_size):
    from .model import NeuralVerifier, ScoreCache

    network = NeuralVerifier(
        corpus, model, pin_path("model-lock.json"), device, batch_size
    )
    with ScoreCache(cache, network) as db:
        yield network, CountedCache(db)


def check_inputs(prepared):
    p = read(protocol_path())
    m = read(Path(prepared) / "manifest.json")
    if m["datasets"] != p["datasets"] or m["sources"] != p["sources"]:
        raise ValueError("prepared release differs from frozen protocol")
    if file_digest(pin_path("model-lock.json")) != p["model"]["lock_sha256"]:
        raise ValueError("model lock differs from protocol")
    return p


def quality(rows, threshold):
    """Exact fraction for threshold ordering, independent of floating-point ties."""
    from .decision import decide
    from .schema import ScoredSpan

    # Called only for fixed calibration traces; acquisition ignores threshold.
    return CLASSES.index(
        decide((ScoredSpan.from_dict(s) for s in rows), threshold).verdict
    )


def calibration_f1(rows, index):
    total = Fraction()
    for label in (0, 1, 2):
        truth = sum(r["label"] == label for r in rows)
        predicted = sum(r["predictions"][index] == label for r in rows)
        tp = sum(r["label"] == r["predictions"][index] == label for r in rows)
        if truth + predicted:
            total += Fraction(2 * tp, truth + predicted)
    return total / 3


def costs(model, verifier, start):
    return {
        "device": model.metadata["device"],
        "batch_size": model.metadata["batch_size"],
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "requested_pairs": verifier.requested,
        "physical_pairs": model.scored_pairs,
        "truncated_physical_pairs": model.truncated_pairs,
        "cache_reused_pairs": verifier.requested - model.scored_pairs,
    }


def calibrate(
    prepared, model, cache, output, *, device="cpu", batch_size=16, warm=True
):
    p = check_inputs(prepared)
    corpus, queries = load_dataset(prepared, "scifact", "calibration")
    source_ids = source_ordinals(corpus)
    index, rows = make_index(corpus), []
    grid = p["calibration"]["grid"]
    start = time.monotonic()
    with runtime(corpus, model, cache, device, batch_size) as (net, verifier):
        if warm:
            prefetch((q["claim"] for q in queries), index, verifier, calibration=True)
        tools = CorpusTools(corpus, index, verifier)
        for q in queries:
            with RunStore(":memory:") as store:
                run = Investigator(tools, store).run(
                    q["claim"], RunConfig("fixed", 8, max(grid), net.model_digest)
                )
            observed = observation(q, run, corpus, source_ids)
            rows.append(
                {
                    "query_id": q["query_id"],
                    "label": observed["label"],
                    "scores": observed["scores"],
                    "predictions": [quality(run["scores"], t) for t in grid],
                }
            )
        values = [calibration_f1(rows, i) for i in range(len(grid))]
        chosen = max(zip(values, grid, strict=True))[1]
        result = {
            "schema_version": 1,
            "protocol_sha256": file_digest(protocol_path()),
            "split_sha256": p["datasets"]["scifact"]["splits"]["calibration"]["sha256"],
            "corpus_digest": corpus.digest,
            "model": net.metadata,
            "model_digest": net.model_digest,
            "grid": grid,
            "macro_f1": [float(v) for v in values],
            "threshold": chosen,
            "queries": rows,
        }
        write(output, result)
        return {
            "threshold": chosen,
            "sha256": file_digest(output),
            "physical": costs(net, verifier, start),
        }


def _query_runs(name, q, tools, threshold, ordinals, p):
    rows, runs = [], {}
    with RunStore(":memory:") as store:
        agent = Investigator(tools, store)
        for policy in p["policies"]:
            for budget in p["budgets"]:
                config = RunConfig(
                    policy, budget, threshold, tools.verifier.model_digest
                )
                rid = checkpoint_id(
                    name,
                    q["query_id"],
                    policy,
                    budget,
                    threshold,
                    config.model_digest,
                    tools.corpus.digest,
                )
                run = agent.run(q["claim"], config, rid)
                rows.append(observation(q, run, tools.corpus, ordinals))
                if name == "scifact":
                    runs[f"{policy}-{budget}"] = run
    return {"records": rows, "runs": runs}


def _demo_ids(queries):
    return sorted(
        (q["query_id"] for q in queries),
        key=lambda s: hashlib.sha256(s.encode()).digest(),
    )[:40]


def csv_bytes(summary):
    out = io.StringIO(newline="")
    fields = [
        "dataset",
        "configuration",
        "n",
        "accuracy",
        "macro_f1",
        "coverage",
        "risk",
        "mean_searches",
        "mean_reads",
        "mean_verifies",
        "mean_nli_pairs",
        "pivotal_rate",
        "quote_integrity",
        "sentence_recall",
        "complete_rationale_rate",
    ]
    writer = csv.DictWriter(out, fields, lineterminator="\n")
    writer.writeheader()
    for name, dataset in sorted(summary["datasets"].items()):
        for key, r in sorted(dataset["configs"].items()):
            writer.writerow(
                dict(dataset=name, configuration=key, **{f: r[f] for f in fields[2:]})
            )
    return out.getvalue().encode()


def _check_query_checkpoint(data, name, query, cal, corpus, p):
    qid = query["query_id"]
    expected = {
        (policy, budget): checkpoint_id(
            name,
            qid,
            policy,
            budget,
            cal["threshold"],
            cal["model_digest"],
            corpus.digest,
        )
        for policy in p["policies"]
        for budget in p["budgets"]
    }
    rows = data["records"]
    expected_runs = (
        {f"{policy}-{budget}" for policy, budget in expected}
        if name == "scifact"
        else set()
    )
    if len(rows) != len(expected) or data["runs"].keys() != expected_runs:
        raise ValueError(f"incomplete checkpoint for {name}/{qid}")
    seen = set()
    for r in rows:
        config = r["policy"], r["budget"]
        if (
            config in seen
            or config not in expected
            or r["run_id"] != expected[config]
            or r["query_id"] != qid
            or r["cluster_id"] != query["cluster_id"]
            or r["label"] != CLASSES.index(query["label"])
        ):
            raise ValueError(f"checkpoint identity mismatch for {name}/{qid}")
        seen.add(config)
        if name == "scifact":
            run = data["runs"][f"{config[0]}-{config[1]}"]
            config_dict = RunConfig(
                *config, cal["threshold"], cal["model_digest"]
            ).to_dict()
            if (
                run["run_id"] != expected[config]
                or run["status"] != "complete"
                or run["claim"] != query["claim"]
                or run["model_digest"] != cal["model_digest"]
                or run["corpus_digest"] != corpus.digest
                or run["config"] != config_dict
            ):
                raise ValueError(
                    f"incomplete or mismatched trace checkpoint for {name}/{qid}"
                )


def _evaluate_dataset(
    prepared,
    name,
    p,
    cal,
    cal_hash,
    model,
    cache,
    out,
    work,
    site,
    stages,
    device,
    batch_size,
    warm,
):
    split = p["evaluation"]["splits"][name]
    corpus, queries = load_dataset(prepared, name, split)
    ordinals = source_ordinals(corpus)
    ckpt = work / digest([cal_hash, name, corpus.digest])
    ckpt.mkdir(parents=True, exist_ok=True)
    records, choices = [], {}
    selection = _demo_ids(queries) if name == "scifact" else []
    # Preserve completed-batch observations across resumed evaluation processes.
    from uuid import uuid4

    sessions = ckpt / "physical"
    sessions.mkdir(exist_ok=True)
    cost_file = sessions / (uuid4().hex + ".json")
    start = time.monotonic()
    with runtime(corpus, model, cache, device, batch_size) as (net, verifier):
        if net.model_digest != cal["model_digest"]:
            raise ValueError("execution model metadata differs from calibration")
        tools = CorpusTools(corpus, make_index(corpus), verifier)
        for batch in batched(queries, 16):
            pending = [
                q for q in batch if not (ckpt / (q["query_id"] + ".json.gz")).is_file()
            ]
            if warm:
                prefetch(
                    (q["claim"] for q in pending),
                    tools.index,
                    verifier,
                    threshold=cal["threshold"],
                )
                write(cost_file, costs(net, verifier, start))
            for q in batch:
                qid = q["query_id"]
                if not qid.isascii() or not qid.isdecimal():
                    raise ValueError("benchmark query ID must be decimal")
                saved = ckpt / (qid + ".json.gz")
                resumed = saved.is_file()
                if resumed:
                    data = read(saved)
                    if digest(data["payload"]) != data["digest"]:
                        raise ValueError("checkpoint integrity mismatch")
                    data = data["payload"]
                else:
                    data = _query_runs(name, q, tools, cal["threshold"], ordinals, p)
                _check_query_checkpoint(data, name, q, cal, corpus, p)
                if not resumed:
                    write(saved, {"digest": digest(data), "payload": data})
                records.extend(data["records"])
                if name == "scifact":
                    write(
                        out / name / "traces" / (qid + ".json.gz"),
                        {"schema_version": 1, "query_id": qid, "runs": data["runs"]},
                        staging=stages[out],
                    )
                    if qid in selection:
                        path = f"claims/{qid}.json"
                        write(
                            site / path,
                            demo_claim(q, data["runs"], corpus),
                            staging=stages[site],
                        )
                        choices[qid] = {
                            "query_id": qid,
                            "claim": q["claim"],
                            "path": path,
                        }
            write(cost_file, costs(net, verifier, start))
        physical = costs(net, verifier, start)
        write(cost_file, physical)
    if name == "scifact":
        for file in ("corpus.jsonl", f"{split}.jsonl"):
            write_bytes(
                out / name / file,
                (Path(prepared) / name / file).read_bytes(),
                staging=stages[out],
            )
        write(
            site / "index.json",
            {
                "schema_version": 1,
                "claims": [choices[qid] for qid in selection],
                "policies": p["policies"],
                "budgets": p["budgets"],
                "threshold": cal["threshold"],
                "corpus_digest": corpus.digest,
                "model_digest": cal["model_digest"],
            },
            staging=stages[site],
        )
    write(
        out / name / "records.json.gz",
        {"schema_version": 1, "records": records},
        staging=stages[out],
    )
    unc = p["evaluation"]["uncertainty"]
    summary = analyze(
        records,
        p["gold_classes"][name],
        draws=unc["draws"],
        seed=unc["seed"],
        claim_sensitivity=name == "climate",
    )
    info = p["datasets"][name]["splits"][split]
    summary.update({k: info[k] for k in ("queries", "clusters", "largest_cluster")})
    return summary, physical, [read(f) for f in sorted(sessions.glob("*.json"))]


def evaluate(
    prepared,
    model,
    cache,
    calibration,
    output,
    *,
    device="cpu",
    batch_size=16,
    work_dir=".artifacts/eval-work",
    site_dir=None,
    warm=True,
):
    from .verify import check_calibration

    p = check_inputs(prepared)
    cal = read(calibration)
    check_calibration(cal, p, prepared_dir=prepared)
    out, work = Path(output), Path(work_dir)
    site = Path(site_dir) if site_dir else out.parent / "site/data"
    cal_hash = file_digest(calibration)
    summary = {
        "schema_version": 1,
        "threshold": cal["threshold"],
        "model_digest": cal["model_digest"],
        "datasets": {},
    }
    physical, sessions = {}, {}
    stages = {root: _public_staging(root, work, (out, site)) for root in (out, site)}
    for name in p["evaluation"]["splits"]:
        summary["datasets"][name], physical[name], sessions[name] = _evaluate_dataset(
            prepared,
            name,
            p,
            cal,
            cal_hash,
            model,
            cache,
            out,
            work,
            site,
            stages,
            device,
            batch_size,
            warm,
        )
    write(out / "summary.json", summary, staging=stages[out])
    write_bytes(out / "summary.csv", csv_bytes(summary), staging=stages[out])
    write(site / "summary.json", summary, staging=stages[site])
    for src, dest in (
        (protocol_path(), out / "protocol.json"),
        (Path(calibration), out / "calibration.json"),
    ):
        if src.resolve() != dest.resolve():
            write_bytes(dest, src.read_bytes(), staging=stages[out])
    write(
        out / "physical.json",
        {"schema_version": 1, "invocation": physical, "sessions": sessions},
        staging=stages[out],
    )
    files = {
        str(f.relative_to(out)): file_digest(f)
        for f in sorted(out.rglob("*"))
        if f.is_file() and f != out / "manifest.json"
    }
    write(
        out / "manifest.json",
        {
            "schema_version": 1,
            "protocol_sha256": file_digest(protocol_path()),
            "calibration_sha256": cal_hash,
            "files": files,
            "site_files": {
                str(f.relative_to(site)): file_digest(f)
                for f in sorted(site.rglob("*"))
                if f.is_file()
            },
        },
        staging=stages[out],
    )
    return {
        "results": str(out),
        "site": str(site),
        "run_count": sum(
            config["n"]
            for dataset in summary["datasets"].values()
            for config in dataset["configs"].values()
        ),
    }
