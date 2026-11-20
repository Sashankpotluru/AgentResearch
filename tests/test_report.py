import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def example():
    from test_engine import components, config

    from agentresearch.engine import Investigator
    from agentresearch.store import RunStore

    tools = components()
    with RunStore(":memory:") as store:
        run = Investigator(tools, store).run(
            "Sea ice <script>x</script> [read](javascript:evil)", config()
        )
    return run, tools.corpus


def test_validate_and_safe_markdown():
    from agentresearch.report import to_markdown, validate_run

    run, corpus = example()
    validate_run(run, corpus)
    md = to_markdown(run, corpus)
    assert "<script>" not in md
    assert "[read](javascript:" not in md
    assert "🧊" in md and "SUPPORTS" in md and "automated" in md.lower()
    for row in run["scores"]:
        assert row["span"]["source_id"] in md


@pytest.mark.parametrize(
    "tamper",
    [
        "chain",
        "scores",
        "model",
        "corpus",
        "quote",
        "verdict",
        "usage",
        "truncated",
        "policy",
        "bool",
    ],
)
def test_reject_corrupted_or_incomplete_binding(tamper):
    from agentresearch.report import validate_run

    run, corpus = example()
    if tamper == "chain":
        run["events"][0]["hash"] = "0" * 64
    elif tamper == "scores":
        run["scores"][0]["entailment"] -= 1
        run["scores"][0]["neutral"] += 1
    elif tamper in ("model", "corpus"):
        run[tamper + "_digest"] = "changed"
    elif tamper == "quote":
        run["events"][1]["output"][0]["quote"] = "unexpected"
    elif tamper == "verdict":
        run["assessment"]["final"]["verdict"] = "REFUTES"
    elif tamper == "usage":
        run["usage"]["nli_pairs"] += 1
    elif tamper == "truncated":
        run["events"].pop()
    elif tamper == "bool":
        run["events"][0]["sequence"] = True
    else:
        run["config"]["policy"] = "fixed"
    with pytest.raises(ValueError):
        validate_run(run, corpus)


def test_report_incomplete_and_cli(tmp_path):
    from test_engine import components, config

    from agentresearch.engine import Investigator
    from agentresearch.report import to_markdown
    from agentresearch.store import RunStore

    tools = components()
    with RunStore(":memory:") as store:
        run = Investigator(tools, store).run("ice", config(), max_steps=2)
    assert "incomplete" in to_markdown(run, tools.corpus).lower()
    r = tmp_path / "run.json"
    c = tmp_path / "corpus.json"
    r.write_text(json.dumps(run))
    c.write_text(json.dumps(tools.corpus.to_dict()))
    for command in ["audit", "report"]:
        out = subprocess.run(
            [sys.executable, "-m", "agentresearch", command, str(r), str(c)],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
            check=False,
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, out.stderr
        assert ("running" if command == "audit" else "incomplete") in out.stdout.lower()


def test_report_identifies_conflict_and_replacement_witnesses():
    from test_engine import components, config

    from agentresearch.engine import Investigator
    from agentresearch.report import to_markdown
    from agentresearch.schema import ScoredSpan, digest
    from agentresearch.store import RunStore

    tools = components()

    def verify(claim, spans):
        rows = []
        for s in spans:
            c, e = {
                ("a", "0"): (0, 980000),
                ("a", "1"): (970000, 0),
                ("b", "0"): (0, 940000),
            }.get((s.source_id, s.sentence_id), (0, 0))
            rows.append(ScoredSpan(s, c, e, 1000000 - c - e, "fixture"))
        return tuple(rows)

    tools.verifier.verify = verify
    with RunStore(":memory:") as store:
        run = Investigator(tools, store).run("sea ice", config("guarded"))
    markdown = to_markdown(run, tools.corpus)
    lines = markdown.splitlines()
    evidence = {}
    for number, row in enumerate(run["scores"], 1):
        s = row["span"]
        span_id = digest(s)
        label = f"E{number}"
        evidence[s["source_id"], s["sentence_id"]] = (
            f'[{label}](#{label.lower()} "{span_id}")'
        )
        assert f"### {label}" in lines
        assert f"Span ID `{span_id}`" in lines

    def assessment_line(prefix):
        return next(line for line in lines if line.startswith(prefix))

    base = assessment_line("Base assessment")
    assert (
        "CONFLICT" in base and evidence["b", "0"] in base and evidence["a", "1"] in base
    )
    final = assessment_line("Final assessment")
    assert "ABSTAIN" in final and "No witnesses" in final
    removed_b = assessment_line("- Removing b ")
    assert "INSUFFICIENT" in removed_b and "Internal conflict" in removed_b
    assert evidence["a", "0"] in removed_b and evidence["a", "1"] in removed_b
    removed_a = assessment_line("- Removing a ")
    assert "SUPPORTS" in removed_a and evidence["b", "0"] in removed_a


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://pubmed.ncbi.nlm.nih.gov/123/", True),
        ("https://999.999.999.999/article", False),
        ("http://127.0.0.1/article", True),
        ("http://127.1/article", True),
        ("https://[2001:db8::1]:8000/article", True),
        ("https://[::ffff:192.0.2.1]/article", True),
        ("javascript:alert(1)", False),
        ("https://user:secret@example.org/x", False),
        ("https://example.org/x>\n# injected", False),
        ("https://example.org/\\bad", False),
        ("https://@example.org", False),
        (None, False),
    ],
)
def test_markdown_links_only_safe_source_urls(url, expected):
    from dataclasses import replace

    from test_engine import components, config

    from agentresearch.engine import Investigator
    from agentresearch.report import to_markdown
    from agentresearch.retrieval import BM25Index
    from agentresearch.schema import Corpus
    from agentresearch.store import RunStore
    from agentresearch.tools import CorpusTools

    base = components()
    corpus = Corpus(tuple(replace(s, url=url) for s in base.corpus.sources))
    tools = CorpusTools(corpus, BM25Index(corpus), base.verifier)
    with RunStore(":memory:") as store:
        run = Investigator(tools, store).run("sea ice", config("fixed"))
    md = to_markdown(run, corpus)
    assert ("[Source record](<" in md) is expected
    if expected:
        assert f"[Source record](<{url}>)" in md
    elif url:
        assert url not in md
    assert all(s["span"]["source_id"] in md for s in run["scores"])


def test_cli_report_bundle_includes_search_only_sources(tmp_path, capsys):
    from agentresearch.cli import main

    run, corpus = example()
    r = tmp_path / "run.json"
    c = tmp_path / "corpus.json"
    r.write_text(json.dumps(run))
    c.write_text(json.dumps(corpus.to_dict()))
    main(["report", str(r), str(c), "--bundle"])
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["schema_version"] == 1
    assert bundle["run"] == run
    assert bundle["view"] == {"excluded_source_ids": []}
    hits = {
        h["source_id"]
        for event in run["events"]
        if event["tool"] == "search"
        for h in event["output"]
    }
    assert hits - {s["span"]["source_id"] for s in run["scores"]}
    assert set(bundle["sources"]) == hits


def test_large_local_trace_cli_round_trip_and_limits(tmp_path):
    from dataclasses import replace

    from agentresearch.data import source_from_lines
    from agentresearch.engine import Investigator
    from agentresearch.retrieval import BM25Index
    from agentresearch.schema import Corpus, RunConfig, ScoredSpan, canonical_bytes
    from agentresearch.store import RunStore
    from agentresearch.tools import CorpusTools

    class PrefixVerifier:
        model_digest = "short-prefix"

        def verify(self, claim, spans):
            (span,) = spans
            prefix = replace(span, end=3, quote="ice")
            return (ScoredSpan(prefix, 0, 1000000, 0, self.model_digest),)

    source = source_from_lines("ice", "Ice", [("1", "ice " + "x" * 2_830_000)])
    corpus = Corpus((source,))
    verifier = PrefixVerifier()
    tools = CorpusTools(corpus, BM25Index(corpus), verifier)
    config = RunConfig("fixed", 1, 900000, verifier.model_digest)
    with RunStore(":memory:") as store:
        run = Investigator(tools, store).run("ice", config)
    run_path, corpus_path = tmp_path / "run.json", tmp_path / "corpus.json"
    run_path.write_bytes(canonical_bytes(run))
    corpus_path.write_bytes(canonical_bytes(corpus.to_dict()))
    assert run["status"] == "complete"
    assert 5.3 < run_path.stat().st_size / 1024**2 < 5.5

    def cli(command, *args):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "agentresearch",
                command,
                run_path,
                corpus_path,
                *args,
            ],
            check=False,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
            capture_output=True,
            text=True,
        )

    audit = cli("audit")
    assert audit.returncode == 0, audit.stderr
    assert json.loads(audit.stdout)["valid"]
    report = cli("report")
    assert report.returncode == 0 and "SUPPORTS" in report.stdout
    limited = cli("audit", "--max-run-bytes", "5242880")
    assert limited.returncode == 2 and "size limit" in limited.stderr
    portable = cli("report", "--bundle")
    assert portable.returncode == 2 and "5 MiB" in portable.stderr


def test_cli_markdown_carries_terms_only_for_the_verified_attack_corpus(
    tmp_path, capsys
):
    from agentresearch.cli import main
    from agentresearch.data import read_corpus
    from agentresearch.experiment import read

    repo = Path(__file__).resolve().parents[1]
    license_text = (repo / "data/cyber-licenses/attack-license.txt").read_text()
    release = read(repo / "site/assets/cyber-report.json")
    corpus_path = repo / "results-cyber/corpus.jsonl"
    corpus = read_corpus(corpus_path)
    assert release["corpus_digest"] == corpus.digest
    assert release["source_digests"] == sorted(s.source_digest for s in corpus.sources)
    demo = read(repo / "site/data/claims/S0366-T1120.json")
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(demo["runs"]["challenge-4"]))
    main(["report", str(run_path), str(corpus_path)])
    report = capsys.readouterr().out
    assert "quote MITRE ATT&CK® procedure summaries" in report
    assert (
        "[Canonical reference](<" in report
        and "Vendor report text was never retrieved" in report
    )
    assert "refutation therefore requires independent factual verification" in report
    assert license_text in report

    generic, other = example()
    other_path = tmp_path / "corpus.json"
    other_path.write_text(json.dumps(other.to_dict()))
    run_path.write_text(json.dumps(generic))
    main(["report", str(run_path), str(other_path)])
    report = capsys.readouterr().out
    assert "## Evidence" in report and "source" in report
    assert "MITRE" not in report and "Canonical reference" not in report
