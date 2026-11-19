import asyncio
import json
import os
import sys
from pathlib import Path

import pytest


def test_official_stdio_client_calls_bounded_structured_tools(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "agentresearch",
            "serve-mcp",
            "--corpus",
            str(root / "tests/fixtures/core.json"),
            "--model-dir",
            str(tmp_path),
            "--lock",
            str(root / "data/model-lock.json"),
            "--db",
            str(tmp_path / "runs.db"),
        ],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )

    async def exercise():
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            assert {t.name for t in (await session.list_tools()).tools} == {
                "search_sources",
                "read_source",
                "investigate",
                "audit_run",
            }
            result = await session.call_tool(
                "search_sources", {"claim": "sea ice", "limit": 1}
            )
            assert (
                not result.is_error and result.structured_content["schema_version"] == 1
            )
            (hit,) = result.structured_content["hits"]
            result = await session.call_tool(
                "read_source", {"claim": "ice", "source_id": hit["source_id"]}
            )
            assert len(result.structured_content["spans"]) == 2
            for name, args in [
                ("read_source", {"claim": "x", "source_id": "missing"}),
                ("search_sources", {"claim": "x" * 10001}),
                ("audit_run", {"run_id": "not-saved"}),
                ("investigate", {"claim": "ice", "budget": 9}),
            ]:
                assert (await session.call_tool(name, args)).is_error

    asyncio.run(exercise())


def test_cli_run_resume_report_audit_with_prepared_jsonl(tmp_path, monkeypatch, capsys):
    from agentresearch import cli, model
    from agentresearch.schema import ScoredSpan

    class Offline:
        model_digest = "local-test"

        def __init__(self, corpus, *args, **kwargs):
            self.corpus = corpus

        def verify(self, claim, spans):
            return tuple(
                ScoredSpan(s, 1000, 995000, 4000, self.model_digest) for s in spans
            )

    monkeypatch.setattr(model, "NeuralVerifier", Offline)
    root = Path(__file__).resolve().parents[1]
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "\n".join(
            map(
                json.dumps,
                json.loads((root / "tests/fixtures/core.json").read_text())["sources"],
            )
        )
    )
    args = [
        "--corpus",
        str(corpus),
        "--model-dir",
        str(tmp_path),
        "--db",
        str(tmp_path / "runs.db"),
    ]
    cli.main(
        [
            "run",
            *args,
            "--claim",
            "Sea ice",
            "--policy",
            "fixed",
            "--budget",
            "1",
            "--threshold",
            "800000",
            "--max-steps",
            "1",
        ]
    )
    run = json.loads(capsys.readouterr().out)
    assert run["status"] == "running"
    cli.main(["resume", *args, "--run-id", run["run_id"]])
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "complete"
    path = tmp_path / "run.json"
    path.write_text(output)
    cli.main(["audit", str(path), str(corpus)])
    assert json.loads(capsys.readouterr().out)["valid"]
    cli.main(["report", str(path), str(corpus)])
    assert "SUPPORTS" in capsys.readouterr().out
