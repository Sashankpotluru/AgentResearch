import asyncio
import json
import os
import sqlite3
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
    cache_path = tmp_path / "scores.db"
    args.extend(["--cache", str(cache_path)])
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
    with sqlite3.connect(cache_path) as cache:
        assert cache.execute("SELECT count(*) FROM scores").fetchone() == (2,)
    path = tmp_path / "run.json"
    path.write_text(output)
    cli.main(["audit", str(path), str(corpus)])
    assert json.loads(capsys.readouterr().out)["valid"]
    cli.main(["report", str(path), str(corpus)])
    assert "SUPPORTS" in capsys.readouterr().out


@pytest.mark.parametrize("alias", ["symlink-parent", "hard-link"])
def test_cli_rejects_equivalent_checkpoint_and_cache_before_start(
    alias, tmp_path, monkeypatch, capsys
):
    from agentresearch import cli, model

    checkpoint = tmp_path / "runs.sqlite"
    if alias == "symlink-parent":
        directory_link = tmp_path / "linked"
        directory_link.symlink_to(tmp_path, target_is_directory=True)
        cache_path = directory_link / "runs.sqlite"
    else:
        checkpoint.touch()
        cache_path = tmp_path / "alternate.sqlite"
        cache_path.hardlink_to(checkpoint)

    def unexpected_model_load(*args):
        pytest.fail("loaded the model before checking database paths")

    monkeypatch.setattr(model, "NeuralVerifier", unexpected_model_load)
    corpus = Path(__file__).parent / "fixtures/core.json"
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "run",
                "--db",
                str(checkpoint),
                "--cache",
                str(cache_path),
                "--corpus",
                str(corpus),
                "--model-dir",
                str(tmp_path),
                "--claim",
                "ice",
                "--budget",
                "1",
                "--policy",
                "fixed",
                "--threshold",
                "800000",
            ]
        )
    assert error.value.code == 2 and "different" in capsys.readouterr().err
    assert not checkpoint.exists() or checkpoint.stat().st_size == 0


def test_prospective_mixed_case_database_paths_follow_volume_semantics(
    tmp_path, monkeypatch, capsys
):
    from agentresearch import cli, model
    from agentresearch.schema import ScoredSpan

    (tmp_path / "CaseProbe").touch()
    aliases = (tmp_path / "caseprobe").exists()
    checkpoint, cache_path = tmp_path / "Ledger.db", tmp_path / "ledger.db"
    assert not checkpoint.exists() and not cache_path.exists()

    class LocalVerifier:
        model_digest = "fixture"

        def __init__(self, corpus, *_):
            if aliases:
                pytest.fail(
                    "loaded the model after SQLite created a case-insensitive alias"
                )
            self.corpus = corpus

        def verify(self, claim, spans):
            return tuple(ScoredSpan(s, 1, 999998, 1, self.model_digest) for s in spans)

    monkeypatch.setattr(model, "NeuralVerifier", LocalVerifier)
    args = [
        "run",
        "--db",
        str(checkpoint),
        "--cache",
        str(cache_path),
        "--corpus",
        str(Path(__file__).parent / "fixtures/core.json"),
        "--model-dir",
        str(tmp_path),
        "--claim",
        "ice",
        "--budget",
        "1",
        "--policy",
        "fixed",
        "--threshold",
        "800000",
    ]
    if aliases:
        with pytest.raises(SystemExit) as error:
            cli.main(args)
        assert error.value.code == 2 and "different" in capsys.readouterr().err
    else:
        cli.main(args)
        assert json.loads(capsys.readouterr().out)["status"] == "complete"
        assert not checkpoint.samefile(cache_path)
        with sqlite3.connect(cache_path) as cache_db:
            assert cache_db.execute("SELECT COUNT(*) FROM scores").fetchone() == (2,)
