"""Deterministic intake and provenance for the two read-only CTI benchmarks."""

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from urllib.request import urlopen

from .data import file_digest, pin_path, source_from_lines
from .experiment import read, write, write_bytes
from .schema import Corpus
from .transport import verified_context


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def protocol():
    return read(pin_path("cyber-protocol.json"))


def intake(directory, *, download=False):
    directory = Path(directory)
    pins = read(pin_path("cyber-sources.json"))
    for pin in pins:
        target = directory / pin["path"]
        if download and not target.exists():
            with urlopen(
                pin["url"], timeout=90, context=verified_context()
            ) as response:
                data = response.read(pin["bytes"] + 1)
            if (
                len(data) != pin["bytes"]
                or hashlib.sha256(data).hexdigest() != pin["sha256"]
            ):
                raise ValueError(f"download differs from pin: {target}")
            write_bytes(target, data)
        if (
            target.stat().st_size != pin["bytes"]
            or file_digest(target) != pin["sha256"]
        ):
            raise ValueError(f"intake differs from pin: {target}")
    return pins


def normalize(text):
    return " ".join(text.split())


def clean(text):
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    return normalize(re.sub(r"\(Citation:[^)]*\)", "", text))


def mitre_id(obj):
    return next(
        ref["external_id"]
        for ref in obj["external_references"]
        if ref["source_name"] == "mitre-attack"
    )


def active(obj):
    return not (obj.get("revoked") or obj.get("x_mitre_deprecated"))


def attack_data(objects, historical):
    malware, definitions, active_tech = {}, {}, {}
    for obj in objects:
        if obj["type"] == "malware" and active(obj):
            malware[obj["id"]] = {
                "id": mitre_id(obj),
                "name": obj["name"],
                "description": clean(obj.get("description", "")),
                "ransomware_related": "ransom" in obj.get("description", "").lower(),
            }
        if obj["type"] == "attack-pattern":
            tid = mitre_id(obj)
            if active(obj) or tid in historical:
                definitions[tid] = {
                    "id": tid,
                    "name": obj["name"],
                    "description": clean(obj["description"]),
                    "historical": not active(obj),
                    "stix_id": obj["id"],
                }
            if active(obj):
                active_tech[obj["id"]] = tid

    procedures = defaultdict(list)
    for obj in objects:
        if (
            obj["type"] != "relationship"
            or obj.get("relationship_type") != "uses"
            or not active(obj)
        ):
            continue
        family = malware.get(obj["source_ref"])
        tid = active_tech.get(obj["target_ref"])
        text = clean(obj.get("description", ""))
        if not (family and tid and text):
            continue
        refs = obj.get("external_references", [])
        procedures[text].append(
            {
                "id": obj["id"],
                "family_id": family["id"],
                "technique_id": tid,
                "references": sorted(
                    refs, key=lambda r: (r.get("url", ""), r["source_name"])
                ),
            }
        )

    papers, excerpts, gold = defaultdict(list), {}, defaultdict(set)
    for text, relations in procedures.items():
        relations.sort(key=lambda row: row["id"])
        refs = sorted(
            (ref["url"], ref.get("description") or ref["source_name"])
            for row in relations
            for ref in row["references"]
            if ref.get("url")
        )
        if not refs:
            family = min(row["family_id"] for row in relations)
            refs = [
                (
                    f"https://attack.mitre.org/software/{family}/",
                    f"MITRE ATT&CK software {family}",
                )
            ]
        url, title = refs[0]
        sid, eid = "ref-" + sha(url)[:20], "excerpt-" + sha(text)[:20]
        papers[url].append((eid, text, title))
        excerpts[eid] = {
            "source_id": sid,
            "text_sha256": sha(text),
            "relationships": relations,
        }
        for row in relations:
            gold[row["family_id"], row["technique_id"]].add(sid)
    sources = []
    for url, snippets in sorted(papers.items()):
        snippets.sort()
        sources.append(
            source_from_lines(
                "ref-" + sha(url)[:20],
                min(s[2] for s in snippets),
                [(eid, text) for eid, text, _ in snippets],
                url,
            )
        )
    families = {f["id"]: f for f in malware.values()}
    queries = []
    for (fid, tid), ids in sorted(gold.items()):
        name, technique = families[fid]["name"], definitions[tid]["name"]
        queries.append(
            {
                "query_id": f"{fid}-{tid}",
                "family_id": fid,
                "family": name,
                "technique_id": tid,
                "technique": technique,
                "ransomware_related": families[fid]["ransomware_related"],
                "query": f"Which reports document {name} using {technique}?",
                "claim": f"{name} uses {technique}.",
                "gold": sorted(ids),
            }
        )
    return Corpus(tuple(sorted(sources, key=lambda s: s.source_id))), {
        "families": families,
        "techniques": definitions,
        "excerpts": excerpts,
        "queries": queries,
    }


def tram_data(rows, definitions):
    merged, links = {}, {}

    def root(doc):
        links.setdefault(doc, doc)
        while links[doc] != doc:
            links[doc] = links[links[doc]]
            doc = links[doc]
        return doc

    for i, row in enumerate(rows):
        text = normalize(row["sentence"])
        if not text or not row["labels"]:
            continue
        if not row["doc_title"].strip() or set(row["labels"]).difference(definitions):
            raise ValueError(f"unknown TRAM label or missing report title at row {i}")
        item = merged.setdefault(text, {"labels": set(), "reports": set(), "rows": []})
        item["labels"].update(row["labels"])
        item["reports"].add(row["doc_title"])
        item["rows"].append(i)
    for item in merged.values():
        reports = sorted(item["reports"])
        for doc in reports:
            left, right = root(reports[0]), root(doc)
            links[max(left, right)] = min(left, right)
    return sorted(
        (
            {
                "query_id": "tram-" + sha(text)[:20],
                "sentence": text,
                "gold": sorted(item["labels"]),
                "reports": sorted(item["reports"]),
                "rows": item["rows"],
                "component": root(min(item["reports"])),
                "ransomware_related": any(
                    "ransom" in d.lower() for d in item["reports"]
                ),
            }
            for text, item in merged.items()
        ),
        key=lambda q: q["query_id"],
    )


def selection(queries, p):
    rank = lambda value: (sha(p["experiment"] + ":" + value), value)
    by_id = {q["query_id"]: q for q in queries}
    ordered = sorted(by_id, key=rank)
    benchmark = []
    for flag, key in ((True, "ransomware_related"), (False, "other")):
        benchmark.extend(
            [qid for qid in ordered if by_id[qid]["ransomware_related"] == flag][
                : p["agent_subset"][key]
            ]
        )
    benchmark += [qid for qid in ordered if qid not in benchmark][
        : p["agent_subset"]["n"] - len(benchmark)
    ]
    representatives = {}
    for qid in sorted(by_id, key=lambda value: (sha(value), value)):
        representatives.setdefault(by_id[qid]["family_id"], qid)
    priority = p["demo"]["priority_families"]

    def order_family(fid):
        name = by_id[representatives[fid]]["family"]
        return (
            priority.index(name) if name in priority else len(priority),
            sha(fid),
            fid,
        )

    demo = [
        representatives[fid]
        for fid in sorted(representatives, key=order_family)[: p["demo"]["n"]]
    ]
    return {"benchmark": benchmark, "demo": demo}


def prepare(raw, work):
    pins = intake(raw)
    p = protocol()
    hashes = {pin["path"]: pin["sha256"] for pin in pins}
    if (hashes["attack.json"], hashes["tram.json"]) != (
        p["attack_sha256"],
        p["tram_sha256"],
    ):
        raise ValueError("protocol and intake pins disagree")
    corpus, catalog = attack_data(
        read(Path(raw) / "attack.json")["objects"], p["legacy_technique_ids_for_tram"]
    )
    rows = read(Path(raw) / "tram.json")
    items = tram_data(rows, catalog["techniques"])
    for name, data in (
        ("corpus", corpus.to_dict()),
        ("catalog", catalog),
        ("tram", items),
    ):
        write(Path(work) / f"{name}.json.gz", data)
    selected = selection(catalog["queries"], p)
    write(Path(work) / "selection.json", selected)
    manifest = {
        "schema_version": 1,
        "input": pins,
        "protocol_sha256": file_digest(pin_path("cyber-protocol.json")),
        "corpus_digest": corpus.digest,
        "tram_input_rows": len(rows),
        "files": {
            name: file_digest(Path(work) / name)
            for name in (
                "corpus.json.gz",
                "catalog.json.gz",
                "tram.json.gz",
                "selection.json",
            )
        },
    }
    write(Path(work) / "manifest.json", manifest)
    return manifest
