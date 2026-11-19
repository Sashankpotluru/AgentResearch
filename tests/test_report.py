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
