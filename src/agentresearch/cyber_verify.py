"""Independent validation of CTI inventory, rankings, sample membership and saved traces."""

import math
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from .cyber_data import prepare, protocol, sha
from .data import file_digest, pin_path, read_corpus
from .experiment import checkpoint_id, make_index, read
from .report import validate_run
from .schema import VERDICTS, Corpus, digest
from .verify import compare, require


def verify_selection(queries, p):
    ordered = sorted(queries, key=lambda q: sha(p["experiment"] + ":" + q["query_id"]))
    benchmark = []
    for flag, k in ((True, "ransomware_related"), (False, "other")):
        benchmark += [
            q["query_id"] for q in ordered if q["ransomware_related"] == flag
        ][: p["agent_subset"][k]]
    benchmark += [q["query_id"] for q in ordered if q["query_id"] not in benchmark][
        : p["agent_subset"]["n"] - len(benchmark)
    ]
    by_family = defaultdict(list)
    for q in queries:
        by_family[q["family_id"]].append(q)
    chosen = {
        fid: min(rows, key=lambda q: sha(q["query_id"]))
        for fid, rows in by_family.items()
    }
    families = [
        fid
        for name in p["demo"]["priority_families"]
        for fid, q in sorted(chosen.items())
        if q["family"] == name
    ]
    families.extend(sorted(set(chosen).difference(families), key=sha))
    return {
        "benchmark": benchmark,
        "demo": [chosen[f]["query_id"] for f in families[: p["demo"]["n"]]],
    }


def ranking_statistics(rows, methods, comparison, p):
    """Compute directly from one-based relevant-item positions."""
    out = {}
    for slice_name, subset in (
        ("all", rows),
        ("ransomware_related", [r for r in rows if r["ransomware_related"]]),
    ):
        metrics, recall = {}, {}
        for method in methods:
            scores = []
            for row in subset:
                gold = set(row["gold"])
                pos = [
                    i for i, sid in enumerate(row["rankings"][method], 1) if sid in gold
                ]
                ideal = sum(
                    1 / math.log2(1 + i) for i in range(1, 1 + min(len(gold), 5))
                )
                scores.append(
                    [
                        len(pos) / len(gold),
                        float(1 in pos),
                        float(bool(pos)),
                        1 / min(pos) if pos else 0,
                        sum(1 / math.log2(i + 1) for i in pos) / ideal,
                    ]
                )
            metrics[method] = dict(
                zip(
                    ("recall@5", "hit@1", "hit@5", "mrr@5", "ndcg@5"),
                    np.mean(scores, axis=0),
                    strict=True,
                )
            )
            recall[method] = [row[0] for row in scores]
        groups = defaultdict(list)
        for i, row in enumerate(subset):
            groups[row["cluster"]].append(recall[comparison][i] - recall["bm25"][i])
        pairs = np.array([[sum(groups[k]), len(groups[k])] for k in sorted(groups)])
        rng = np.random.default_rng(p["bootstrap"]["seed"])
        sample = rng.integers(len(pairs), size=(p["bootstrap"]["draws"], len(pairs)))
        result = pairs[sample].sum(axis=1)
        lo, hi = np.quantile(result[:, 0] / result[:, 1], (0.025, 0.975))
        out[slice_name] = {
            "queries": len(subset),
            "clusters": len(pairs),
            "largest_cluster": int(pairs[:, 1].max()),
            "metrics": metrics,
            "paired_recall_at_5": {
                "comparison": comparison + " minus bm25",
                "difference": float(pairs[:, 0].sum() / len(subset)),
                "ci95": [float(lo), float(hi)],
            },
        }
    return out


def check_rankings(rows, expected, candidates, methods):
    require(
        [r["query_id"] for r in rows] == list(expected), "query coverage/order differs"
    )
    for row in rows:
        gold, cluster, flag = expected[row["query_id"]]
        require(
            set(row)
            == {"query_id", "gold", "cluster", "ransomware_related", "rankings"},
            "unexpected ranking fields",
        )
        require(
            (row["gold"], row["cluster"], row["ransomware_related"])
            == (gold, cluster, flag),
            "ranking provenance differs",
        )
        require(
            bool(gold) and len(set(gold)) == len(gold) and set(gold) <= candidates,
            "invalid gold membership",
        )
        require(set(row["rankings"]) == set(methods), "ranking methods differ")
        for ranking in row["rankings"].values():
            require(
                len(ranking) <= 5
                and len(set(ranking)) == len(ranking)
                and set(ranking) <= candidates,
                "invalid ranking",
            )


def agent_statistics(runs):
    results = {}
    for key in runs[0]:
        sample = [r[key] for r in runs]
        verdicts = Counter(run["assessment"]["final"]["verdict"] for run in sample)
        n = len(sample)
        pivotal = sum(
            any(
                v["verdict"] != run["assessment"]["base"]["verdict"]
                for v in run["assessment"]["withdrawals"].values()
            )
            for run in sample
        )
        counts = Counter(
            e["tool"] for run in sample for e in run["events"] if e["error"] is None
        )
        results[key] = {
            "cases": n,
            "verdicts": {v: verdicts[v] for v in VERDICTS},
            "pivotal_cases": pivotal,
            "reads": counts["read"],
            "verifications": counts["verify"],
            "nli_pairs": sum(len(r["scores"]) for r in sample),
            "support_yield": verdicts["SUPPORTS"] / n,
            "abstain_rate": verdicts["ABSTAIN"] / n,
            "pivotal_rate": pivotal / n,
        }
    return results


def check_catalog(cat, p):
    gold = defaultdict(set)
    seen_rels, seen_excerpts = set(), set()
    for sid, source in cat["sources"].items():
        last = 0
        for span in source["sentences"]:
            eid = span["sentence_id"]
            require(
                eid not in seen_excerpts and last <= span["start"] < span["end"],
                "invalid excerpt offsets",
            )
            seen_excerpts.add(eid)
            last = span["end"]
            entry = cat["excerpts"][eid]
            require(
                sid == entry["source_id"]
                and eid == "excerpt-" + entry["text_sha256"][:20],
                "source provenance mismatch",
            )
            for rel in entry["relationships"]:
                fid, tid = rel["family_id"], rel["technique_id"]
                require(
                    fid in cat["families"]
                    and tid in cat["techniques"]
                    and not cat["techniques"][tid]["historical"],
                    "invalid malware relationship",
                )
                require(rel["id"] not in seen_rels, "duplicate relationship")
                seen_rels.add(rel["id"])
                gold[f"{fid}-{tid}"].add(sid)
    require(seen_excerpts == set(cat["excerpts"]), "orphaned excerpt")
    queries = cat["queries"]
    require(
        len(queries) == len(gold) and {q["query_id"] for q in queries} == set(gold),
        "constructed query coverage differs",
    )
    for q in queries:
        require(
            sorted(gold[q["query_id"]]) == q["gold"], "gold differs from relationships"
        )
        family = cat["families"][q["family_id"]]
        names = {
            "family": family["name"],
            "technique": cat["techniques"][q["technique_id"]]["name"],
        }
        require(
            q["query_id"] == q["family_id"] + "-" + q["technique_id"]
            and q["ransomware_related"] == family["ransomware_related"]
            and all(q[k] == v for k, v in names.items())
            and q["claim"] == p["agent_claim"].format(**names)
            and q["query"] == p["malware_query"].format(**names),
            "constructed query metadata differs",
        )
    rows = [i for q in cat["tram"] for i in q["rows"]]
    require(
        len(rows) == len(set(rows)) and min(rows) >= 0,
        "duplicate or negative original TRAM row",
    )
    for q in cat["tram"]:
        require(
            q["query_id"] == "tram-" + q["sentence_sha256"][:20]
            and q["reports"]
            and q["rows"]
            and q["ransomware_related"]
            == any("ransom" in d.lower() for d in q["reports"]),
            "invalid TRAM metadata",
        )
    return len(seen_rels)


def verify(out, prepared=None, *, strict_intake=None, site_dir=None):
    out = Path(out)
    if strict_intake is not None:
        with TemporaryDirectory() as tmp:
            prepare(strict_intake, tmp)
            return verify(out, Path(tmp), site_dir=site_dir)
    m = read(out / "manifest.json")
    for name, key in (
        ("cyber-protocol.json", "protocol_sha256"),
        ("cyber-sources.json", "source_pins_sha256"),
        ("model-lock.json", "model_lock_sha256"),
    ):
        require(file_digest(pin_path(name)) == m[key], "release pin differs: " + name)
    require(
        m["intake"] == read(pin_path("cyber-sources.json")), "intake identity differs"
    )
    actual = {
        f.relative_to(out).as_posix(): file_digest(f)
        for f in out.rglob("*")
        if f.is_file() and f != out / "manifest.json"
    }
    require(m["files"] == actual, "public artifact inventory or checksum differs")
    p = protocol()
    meta, summary, selected, cat = (
        read(out / name)
        for name in ("model.json", "summary.json", "selection.json", "catalog.json.gz")
    )
    by_id = {q["query_id"]: q for q in cat["queries"]}
    require(
        meta["lock"] == read(pin_path("model-lock.json"))
        and digest(meta) == summary["model_digest"]
        and meta["lock"]["revision"] == p["agent_model_revision"],
        "model identity differs",
    )
    require(
        summary["threshold"] == p["agent_threshold"]
        and summary["corpus_digest"] == m["prepared"]["corpus_digest"],
        "experiment identity differs",
    )
    compare(selected, verify_selection(cat["queries"], p), "selection")
    qids = set(selected["benchmark"] + selected["demo"])
    require(
        set(actual)
        == {
            "model.json",
            "catalog.json.gz",
            "summary.json",
            "attack-rankings.json.gz",
            "tram-rankings.json.gz",
            "selection.json",
        }
        | {f"traces/{qid}.json.gz" for qid in qids}
        | {"corpus.jsonl", "prepared/manifest.json"}
        | {f"prepared/{name}" for name in m["prepared"]["files"]},
        "unexpected release files",
    )
    require(
        check_catalog(cat, p) == summary["counts"]["relationships"],
        "relationship count differs",
    )
    prepared = Path(prepared) if prepared is not None else out / "prepared"
    strict = True
    if strict:
        prepared = Path(prepared)
        require(
            m["prepared"] == read(prepared / "manifest.json"),
            "prepared manifest differs",
        )
        require(
            all(
                file_digest(prepared / name) == sig
                for name, sig in m["prepared"]["files"].items()
            ),
            "prepared hash differs",
        )
        corpus = Corpus.from_dict(read(prepared / "corpus.json.gz"))
        require(
            corpus.digest == summary["corpus_digest"], "prepared corpus digest differs"
        )
        require(
            read_corpus(out / "corpus.jsonl").digest == corpus.digest,
            "CLI corpus differs from evaluated corpus",
        )
        require(
            read(out / "prepared" / "manifest.json") == m["prepared"],
            "bundled preparation manifest differs",
        )
        require(
            all(
                file_digest(out / "prepared" / name) == sig
                for name, sig in m["prepared"]["files"].items()
            ),
            "bundled preparation differs",
        )
        index = make_index(corpus)
        from .cyber_experiment import corpus_catalog, counts

        original = read(prepared / "catalog.json.gz")
        items = read(prepared / "tram.json.gz")
        require(
            corpus_catalog(corpus, original, items) == cat,
            "public provenance differs from original corpus",
        )
        compare(
            summary["counts"],
            counts(corpus, original, items, m["prepared"]["tram_input_rows"]),
            "counts",
        )
    for name, queries, cluster, candidates, comparison in (
        ("attack", cat["queries"], "family_id", cat["sources"], "family_bm25"),
        ("tram", cat["tram"], "component", cat["techniques"], "rrf"),
    ):
        rows = read(out / f"{name}-rankings.json.gz")
        expected = {
            q["query_id"]: (q["gold"], q[cluster], q["ransomware_related"])
            for q in queries
        }
        require(len(expected) == len(queries), "duplicate query ID")
        check_rankings(rows, expected, set(candidates), p[name + "_methods"])
        compare(
            summary["retrieval"][name],
            ranking_statistics(rows, p[name + "_methods"], comparison, p),
            name,
        )
        if strict:
            from .cyber_retrieval import attack_rankings, tram_rankings

            replay = (
                attack_rankings(corpus, original)
                if name == "attack"
                else tram_rankings(original["techniques"], items)
            )
            require(rows == replay, "lexical ranking replay differs")
    grouped = {}
    for qid in sorted(qids):
        trace = read(out / f"traces/{qid}.json.gz")
        require(
            trace["query_id"] == qid and trace["encoding"] == "canonical-runs-v1",
            "trace identity differs",
        )
        runs = trace["runs"]
        require(
            set(runs)
            == {
                f"{pol}-{b}" for pol in p["agent_policies"] for b in p["agent_budgets"]
            },
            "policy/budget grid differs",
        )
        for key, run in runs.items():
            policy, budget = key.rsplit("-", 1)
            expected = checkpoint_id(
                p["experiment"],
                qid,
                policy,
                int(budget),
                p["agent_threshold"],
                summary["model_digest"],
                summary["corpus_digest"],
            )
            require(
                run["status"] == "complete"
                and run["run_id"] == expected
                and run["claim"] == by_id[qid]["claim"]
                and run["config"]
                == {
                    "policy": policy,
                    "budget": int(budget),
                    "threshold": p["agent_threshold"],
                    "model_digest": summary["model_digest"],
                },
                "trace binding differs",
            )
            if strict:
                validate_run(run, corpus, index=index)
        grouped[qid] = runs
    for name, members in selected.items():
        compare(
            summary["agent"][name],
            agent_statistics([grouped[q] for q in members]),
            name,
        )
    if site_dir is not None:
        verify_demo(Path(site_dir), m, summary, selected, original, corpus, grouped, p)
    return {
        "verified": True,
        "original_corpus_replay": strict,
        "unique_runs": len(qids) * len(p["agent_policies"]) * len(p["agent_budgets"]),
        "benchmark_cases": len(selected["benchmark"]),
        "demo_cases": len(selected["demo"]),
        "browser_payloads_verified": site_dir is not None,
    }


def verify_demo(site, manifest, summary, selected, catalog, corpus, grouped, p):
    """Cross-bind browser data to the complete public research release."""
    actual = {
        f.relative_to(site).as_posix(): file_digest(f)
        for f in site.rglob("*")
        if f.is_file()
    }
    expected_files = {"index.json", "summary.json"} | {
        f"claims/{qid}.json" for qid in selected["demo"]
    }
    require(
        set(actual) == expected_files and actual == manifest["site_files"],
        "browser inventory or checksum differs",
    )
    compare(read(site / "summary.json"), summary, "browser summary")
    index = read(site / "index.json")
    require(
        index["schema_version"] == 1
        and index["corpus_digest"] == corpus.digest
        and index["model_digest"] == summary["model_digest"]
        and index["threshold"] == p["agent_threshold"]
        and index["policies"] == p["agent_policies"]
        and index["budgets"] == p["agent_budgets"],
        "browser experiment differs",
    )
    require(
        [row["query_id"] for row in index["claims"]] == selected["demo"],
        "browser sample differs",
    )
    queries = {q["query_id"]: q for q in catalog["queries"]}
    for row in index["claims"]:
        qid = row["query_id"]
        query = queries[qid]
        relative = f"claims/{qid}.json"
        require(row["path"] == relative, "browser path differs")
        record = read(site / relative)
        require(record["schema_version"] == 1, "browser schema differs")
        for key in (
            "query_id",
            "claim",
            "family",
            "family_id",
            "technique",
            "technique_id",
            "ransomware_related",
        ):
            require(
                record[key] == query[key] and row[key] == query[key],
                "browser case metadata differs",
            )
        require(
            record["description"]
            == catalog["families"][query["family_id"]]["description"],
            "browser family description differs",
        )
        require(
            record["family_url"]
            == f"https://attack.mitre.org/software/{query['family_id']}/"
            and record["technique_url"]
            == "https://attack.mitre.org/techniques/"
            + query["technique_id"].replace(".", "/")
            + "/",
            "browser reference differs",
        )
        compare(record["runs"], grouped[qid], "browser trace")
        ids = {
            hit["source_id"]
            for run in grouped[qid].values()
            for event in run["events"]
            if event["tool"] == "search" and event["error"] is None
            for hit in event["output"]
        }
        compare(
            record["sources"],
            {sid: corpus.by_id[sid].to_dict() for sid in ids},
            "browser sources",
        )
