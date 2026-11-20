"""Run the frozen research protocol on read-only documents and saved model scores."""

from collections import Counter

from .cyber_data import protocol, selection
from .data import file_digest, pin_path
from .engine import Investigator
from .experiment import checkpoint_id, make_index, prefetch, read, runtime, write
from .schema import VERDICTS, Corpus, RunConfig
from .store import RunStore
from .tools import CorpusTools


def run_cases(prepared, work, model, device, batch_size):
    p = protocol()
    work.mkdir(exist_ok=True, parents=True)
    corpus = Corpus.from_dict(read(prepared / "corpus.json.gz"))
    queries = read(prepared / "catalog.json.gz")["queries"]
    by_id = {q["query_id"]: q for q in queries}
    selected = read(prepared / "selection.json")
    if selected != selection(queries, p):
        raise ValueError("selection differs from frozen protocol")
    unique = list(dict.fromkeys(selected["benchmark"] + selected["demo"]))
    index = make_index(corpus)
    with (
        runtime(corpus, model, work / "scores.sqlite", device, batch_size) as (
            network,
            verifier,
        ),
        RunStore(work / "runs.sqlite") as store,
    ):
        if network.metadata["lock"]["revision"] != p["agent_model_revision"]:
            raise ValueError("unexpected model revision")
        write(work / "model.json", network.metadata)
        agent = Investigator(CorpusTools(corpus, index, verifier), store)
        for start in range(0, len(unique), 8):
            chunk = unique[start : start + 8]
            prefetch(
                [by_id[qid]["claim"] for qid in chunk],
                index,
                verifier,
                threshold=p["agent_threshold"],
            )
            for qid in chunk:
                claim = by_id[qid]["claim"]
                runs = {}
                for pol in p["agent_policies"]:
                    for budget in p["agent_budgets"]:
                        cfg = RunConfig(
                            pol, budget, p["agent_threshold"], network.model_digest
                        )
                        run_id = checkpoint_id(
                            p["experiment"],
                            qid,
                            pol,
                            budget,
                            cfg.threshold,
                            cfg.model_digest,
                            corpus.digest,
                        )
                        run = agent.run(claim, cfg, run_id)
                        if run["status"] != "complete":
                            raise RuntimeError(
                                f"model investigation failed: {qid} {pol} {budget}"
                            )
                        runs[f"{pol}-{budget}"] = run
                write(
                    work / "traces" / f"{qid}.json.gz", {"query_id": qid, "runs": runs}
                )
            print(
                f"Model cases {min(start + 8, len(unique))}/{len(unique)}", flush=True
            )


def summarize_runs(groups):
    result = {}
    for group in groups:
        for key, run in group["runs"].items():
            counts = result.setdefault(
                key,
                {
                    "verdicts": dict.fromkeys(VERDICTS, 0),
                    "cases": 0,
                    "pivotal_cases": 0,
                    "reads": 0,
                    "verifications": 0,
                    "nli_pairs": 0,
                },
            )
            counts["cases"] += 1
            counts["verdicts"][run["assessment"]["final"]["verdict"]] += 1
            counts["pivotal_cases"] += bool(run["assessment"]["pivotal_sources"])
            counts["reads"] += run["usage"]["successes"]["read"]
            counts["verifications"] += run["usage"]["successes"]["verify"]
            counts["nli_pairs"] += run["usage"]["nli_pairs"]
    for counts in result.values():
        n = counts["cases"]
        counts["support_yield"] = counts["verdicts"]["SUPPORTS"] / n
        counts["abstain_rate"] = counts["verdicts"]["ABSTAIN"] / n
        counts["pivotal_rate"] = counts["pivotal_cases"] / n
    return result


def corpus_catalog(corpus, catalog, items):
    """Bind public identifiers and offsets to exact upstream strings."""
    from .cyber_data import sha

    sources = {
        s.source_id: {
            "title": s.title,
            "url": s.url,
            "digest": s.source_digest,
            "sentences": [span.to_dict() for span in s.sentences],
        }
        for s in corpus.sources
    }

    def metadata(entries):
        return {
            sid: {
                **{k: v for k, v in entry.items() if k != "description"},
                "description_sha256": sha(entry["description"]),
            }
            for sid, entry in entries.items()
        }

    return {
        "schema_version": 1,
        "sources": sources,
        "families": metadata(catalog["families"]),
        "techniques": metadata(catalog["techniques"]),
        "excerpts": catalog["excerpts"],
        "queries": catalog["queries"],
        "tram": [
            {
                **{k: v for k, v in item.items() if k != "sentence"},
                "sentence_sha256": sha(item["sentence"]),
            }
            for item in items
        ],
    }


def encode_trace(obj):
    """Lossless against pinned inputs. Retain offsets, scores and original event hashes."""
    if isinstance(obj, list):
        return [encode_trace(v) for v in obj]
    if isinstance(obj, dict):
        return {
            k: encode_trace(v) for k, v in obj.items() if k not in {"quote", "snippet"}
        }
    return obj


def counts(corpus, catalog, items, raw_rows):
    import re

    hist = {tid for tid, d in catalog["techniques"].items() if d["historical"]}
    historical = Counter(tid for q in items for tid in q["gold"] if tid in hist)
    return {
        "malware": len(catalog["families"]),
        "malware_with_queries": len({q["family_id"] for q in catalog["queries"]}),
        "sources": len(corpus.sources),
        "unique_procedures": len(catalog["excerpts"]),
        "relationships": sum(
            len(e["relationships"]) for e in catalog["excerpts"].values()
        ),
        "techniques_active": len(catalog["techniques"]) - len(hist),
        "techniques_historical": sorted(hist),
        "tram_raw_rows": raw_rows,
        "tram_labeled_nonblank_rows": sum(len(q["rows"]) for q in items),
        "tram_unique_labeled": len(items),
        "tram_reports": len({d for q in items for d in q["reports"]}),
        "tram_gold_cardinality": dict(
            sorted(Counter(str(len(q["gold"])) for q in items).items())
        ),
        "tram_historical_items": sum(bool(hist.intersection(q["gold"])) for q in items),
        "tram_historical_labels": dict(sorted(historical.items())),
        "tram_explicit_id_items": sum(
            bool(re.search(r"\bT\d{4}(?:\.\d{3})?\b", q["sentence"])) for q in items
        ),
    }


def evaluate(prepared, work, out, model, device, batch_size):
    from .cyber_retrieval import attack_rankings, retrieval_summary, tram_rankings
    from .schema import digest

    manifest = read(prepared / "manifest.json")
    for name, sha256 in manifest["files"].items():
        if file_digest(prepared / name) != sha256:
            raise ValueError(f"prepared artifact changed: {name}")
    p = protocol()
    corpus = Corpus.from_dict(read(prepared / "corpus.json.gz"))
    catalog, items = read(prepared / "catalog.json.gz"), read(prepared / "tram.json.gz")
    selected = read(prepared / "selection.json")
    write(out / "selection.json", selected)
    if corpus.digest != manifest["corpus_digest"] or selected != selection(
        catalog["queries"], p
    ):
        raise ValueError("invalid prepared corpus or selection")
    run_cases(prepared, work, model, device, batch_size)
    meta = read(work / "model.json")
    write(out / "model.json", meta)
    write(out / "catalog.json.gz", corpus_catalog(corpus, catalog, items))
    summary = {
        "schema_version": 1,
        "corpus_digest": corpus.digest,
        "model_digest": digest(meta),
        "threshold": p["agent_threshold"],
        "counts": counts(corpus, catalog, items, manifest["tram_input_rows"]),
        "agent": {},
        "retrieval": {},
        "limitations": p["limitations"]
        + [
            "Some original TRAM sentences mention ATT&CK IDs and include report extraction artifacts.",
            "Family-filtered BM25 uses public malware-to-source membership unavailable to the global comparator.",
            "Trace strings are represented by references to the pinned corpus and Unicode offsets.",
        ],
    }

    for name, rows, contrast in (
        ("attack", attack_rankings(corpus, catalog), "family_bm25"),
        ("tram", tram_rankings(catalog["techniques"], items), "rrf"),
    ):
        write(out / f"{name}-rankings.json.gz", rows)
        summary["retrieval"][name] = retrieval_summary(rows, contrast, p)
    for group, ids in selected.items():
        summary["agent"][group] = summarize_runs(
            read(work / "traces" / f"{qid}.json.gz") for qid in ids
        )
    for qid in sorted(set(selected["benchmark"] + selected["demo"])):
        group = read(work / "traces" / f"{qid}.json.gz")
        write(
            out / "traces" / f"{qid}.json.gz",
            {"encoding": "pinned-corpus-offsets-v1", **encode_trace(group)},
        )
    write(out / "summary.json", summary)
    files = [
        out / f
        for f in (
            "model.json",
            "catalog.json.gz",
            "selection.json",
            "attack-rankings.json.gz",
            "tram-rankings.json.gz",
            "summary.json",
        )
    ]
    files.extend(
        out / "traces" / f"{qid}.json.gz"
        for qid in set(selected["benchmark"] + selected["demo"])
    )
    release = {
        "schema_version": 1,
        "protocol_sha256": file_digest(pin_path("cyber-protocol.json")),
        "model_lock_sha256": file_digest(pin_path("model-lock.json")),
        "source_pins_sha256": file_digest(pin_path("cyber-sources.json")),
        "intake": manifest["input"],
        "prepared": manifest,
        "files": {str(f.relative_to(out)): file_digest(f) for f in sorted(files)},
    }
    write(out / "manifest.json", release)
    return summary
