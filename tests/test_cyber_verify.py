import pytest
from test_report import example

from agentresearch.cyber_verify import verify
from agentresearch.experiment import read, write
from agentresearch.report import validate_run


def test_canonical_trace_keeps_exact_quotes_and_rejects_tampering():
    run, corpus = example()
    validate_run(run, corpus)
    run["scores"][0]["span"]["quote"] += " altered"
    with pytest.raises(ValueError):
        validate_run(run, corpus)


@pytest.mark.parametrize(
    "target",
    ["summary.json", "attack-rankings.json.gz", "traces/fake.json.gz", "extra"],
)
def test_corrupted_or_unexpected_inventory(tmp_path, target):
    from agentresearch.data import file_digest, pin_path

    manifest = {"files": {}, "intake": read(pin_path("cyber-sources.json"))}
    for key, name in [
        ("source_pins_sha256", "cyber-sources.json"),
        ("protocol_sha256", "cyber-protocol.json"),
        ("model_lock_sha256", "model-lock.json"),
    ]:
        manifest[key] = file_digest(pin_path(name))
    write(tmp_path / "manifest.json", manifest)
    write(tmp_path / target, {"corrupted": True})
    with pytest.raises(ValueError, match="inventory"):
        verify(tmp_path)


@pytest.mark.parametrize("name", ["summary.json", "attack-rankings.json.gz"])
def test_resealed_metrics_or_rankings_fail_independent_recomputation(tmp_path, name):
    import os
    import shutil
    from pathlib import Path

    from agentresearch.data import file_digest

    original = Path(__file__).resolve().parents[1] / "results-cyber"
    release = tmp_path / "release"
    shutil.copytree(original, release, copy_function=os.link)
    changed = read(release / name)
    if name == "summary.json":
        changed["retrieval"]["attack"]["all"]["metrics"]["bm25"]["recall@5"] = -1
    else:
        row = next(
            q for q in changed if set(q["gold"]).intersection(q["rankings"]["bm25"])
        )
        row["rankings"]["bm25"] = []
    write(release / name, changed)
    manifest = read(release / "manifest.json")
    manifest["files"][name] = file_digest(release / name)
    write(release / "manifest.json", manifest)
    with pytest.raises(ValueError, match="numeric mismatch"):
        verify(release)
