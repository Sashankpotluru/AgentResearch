"""Publish exact research records for offline replay and the browser casebook.

Procedure text is quoted data. No command or linked report is executed or fetched.
"""

from pathlib import Path

from .cyber_data import protocol, selection
from .data import file_digest
from .experiment import encoded, read, write, write_bytes
from .schema import Corpus

SOURCE_NOTE = (
    "Evidence excerpts are MITRE ATT&CK procedure summaries. Publication links "
    "identify cited references; the original vendor reports were not downloaded."
)


def demo_record(query, runs, corpus, catalog):
    source_ids = {
        hit["source_id"]
        for run in runs.values()
        for event in run["events"]
        if event["tool"] == "search" and event["error"] is None
        for hit in event["output"]
    }
    return {
        "schema_version": 1,
        **{
            key: query[key]
            for key in (
                "query_id",
                "claim",
                "family",
                "family_id",
                "technique",
                "technique_id",
                "ransomware_related",
            )
        },
        "description": catalog["families"][query["family_id"]]["description"],
        "family_url": f"https://attack.mitre.org/software/{query['family_id']}/",
        "technique_url": "https://attack.mitre.org/techniques/"
        + query["technique_id"].replace(".", "/")
        + "/",
        "source_note": SOURCE_NOTE,
        "runs": runs,
        "sources": {sid: corpus.by_id[sid].to_dict() for sid in sorted(source_ids)},
    }


def publish(prepared, work, out, site_dir):
    """Package an already evaluated run; never perform or substitute inference."""
    prepared, work, out, site_dir = map(Path, (prepared, work, out, site_dir))
    manifest = read(out / "manifest.json")
    prepared_manifest = read(prepared / "manifest.json")
    if manifest["prepared"] != prepared_manifest:
        raise ValueError("publication preparation differs from evaluated inputs")
    for name, expected in prepared_manifest["files"].items():
        if file_digest(prepared / name) != expected:
            raise ValueError("changed prepared file: " + name)
    corpus = Corpus.from_dict(read(prepared / "corpus.json.gz"))
    catalog = read(prepared / "catalog.json.gz")
    chosen, p = read(out / "selection.json"), protocol()
    summary = read(out / "summary.json")
    if (
        chosen != selection(catalog["queries"], p)
        or corpus.digest != summary["corpus_digest"]
    ):
        raise ValueError("publication cohort or corpus differs")
    by_id = {q["query_id"]: q for q in catalog["queries"]}
    site_files = {"index.json", "summary.json"} | {
        f"claims/{qid}.json" for qid in chosen["demo"]
    }
    existing = {
        f.relative_to(site_dir).as_posix() for f in site_dir.rglob("*") if f.is_file()
    }
    if existing - site_files:
        raise ValueError(
            "demo destination contains unexpected files; use a fresh directory"
        )
    release_files = set(manifest["files"])
    for name in [*prepared_manifest["files"], "manifest.json"]:
        destination = out / "prepared" / name
        write_bytes(destination, (prepared / name).read_bytes())
        release_files.add(destination.relative_to(out).as_posix())
    write_bytes(
        out / "corpus.jsonl", b"".join(encoded(s.to_dict()) for s in corpus.sources)
    )
    release_files.add("corpus.jsonl")
    summary["limitations"] = [
        note
        for note in summary["limitations"]
        if note
        != "Trace strings are represented by references to the pinned corpus and Unicode offsets."
    ]
    write(out / "summary.json", summary)
    claims = []
    for qid in sorted(set(chosen["benchmark"] + chosen["demo"])):
        group = read(work / "traces" / f"{qid}.json.gz")
        if group["query_id"] != qid:
            raise ValueError("source trace ID differs")
        write(
            out / "traces" / f"{qid}.json.gz",
            {"encoding": "canonical-runs-v1", **group},
        )
    for qid in chosen["demo"]:
        query = by_id[qid]
        group = read(out / "traces" / f"{qid}.json.gz")
        record = demo_record(query, group["runs"], corpus, catalog)
        relative = f"claims/{qid}.json"
        write(site_dir / relative, record)
        claims.append(
            {
                **{
                    key: record[key]
                    for key in (
                        "query_id",
                        "claim",
                        "family",
                        "family_id",
                        "technique",
                        "technique_id",
                        "ransomware_related",
                    )
                },
                "path": relative,
            }
        )
    write(
        site_dir / "index.json",
        {
            "schema_version": 1,
            "dataset": "MITRE ATT&CK 20.1",
            "claims": claims,
            "policies": p["agent_policies"],
            "budgets": p["agent_budgets"],
            "threshold": p["agent_threshold"],
            "corpus_digest": corpus.digest,
            "model_digest": summary["model_digest"],
            "source_note": SOURCE_NOTE,
            "selection_note": "24 illustrative cases; separate from the 64-case benchmark.",
        },
    )
    write(site_dir / "summary.json", summary)
    manifest["files"] = {
        name: file_digest(out / name) for name in sorted(release_files)
    }
    manifest["site_files"] = {
        name: file_digest(site_dir / name) for name in sorted(site_files)
    }
    write(out / "manifest.json", manifest)
    return {"published_cases": len(claims), "corpus_sources": len(corpus.sources)}
