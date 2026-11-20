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
        ("diagnostic_protocol_sha256", "cyber-diagnostic-protocol.json"),
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


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("extra", "prepared inventory"),
        ("nested_protocol", "nested preparation pins"),
        ("nested_input", "nested preparation pins"),
        ("selection", "prepared selection"),
        ("limitation", "scientific limitations"),
        ("diagnostic_pin", "release pin differs"),
        ("diagnostic_label", "identifier diagnostic disclosure"),
    ],
)
def test_resealed_provenance_discrepancies(tmp_path, mutation, reason):
    import os
    import shutil
    from pathlib import Path

    from agentresearch.data import file_digest

    root = Path(__file__).resolve().parents[1] / "results-cyber"
    release = tmp_path / "release"
    shutil.copytree(root, release, copy_function=os.link)
    manifest = read(release / "manifest.json")
    nested = manifest["prepared"]
    if mutation in {"limitation", "diagnostic_label"}:
        summary = read(release / "summary.json")
        if mutation == "limitation":
            summary["limitations"].pop(1)
        else:
            summary["identifier_diagnostic"]["status"] = "pre_registered"
        write(release / "summary.json", summary)
        manifest["files"]["summary.json"] = file_digest(release / "summary.json")
    elif mutation == "diagnostic_pin":
        manifest["diagnostic_protocol_sha256"] = "0" * 64
    else:
        if mutation == "extra":
            key = "extra.json"
            write(release / "prepared" / key, {"extra": True})
        elif mutation == "selection":
            key = "selection.json"
            selected = read(release / "prepared" / key)
            selected["benchmark"].reverse()
            write(release / "prepared" / key, selected)
        elif mutation == "nested_protocol":
            nested["protocol_sha256"] = "0" * 64
        else:
            nested["input"] = []
        if mutation in {"extra", "selection"}:
            nested["files"][key] = file_digest(release / "prepared" / key)
            manifest["files"]["prepared/" + key] = nested["files"][key]
        write(release / "prepared/manifest.json", nested)
        manifest["files"]["prepared/manifest.json"] = file_digest(
            release / "prepared/manifest.json"
        )
    write(release / "manifest.json", manifest)
    with pytest.raises(ValueError, match=reason):
        verify(release)


def test_rebuilt_preparation_accepts_only_matching_decoded_content_and_metadata(
    tmp_path,
):
    import gzip

    from agentresearch.cyber_data import PREPARED_FILES
    from agentresearch.cyber_verify import check_rebuilt_preparation
    from agentresearch.data import file_digest

    bundled = tmp_path / "release"
    rebuilt = tmp_path / "rebuilt"
    for root, mtime in ((bundled, 0), (rebuilt, 12345)):
        root.mkdir()
        for name in PREPARED_FILES:
            payload = b'{"key":1}\n'
            if name.endswith(".gz"):
                payload = gzip.compress(payload, mtime=mtime)
            (root / name).write_bytes(payload)
        write(
            root / "manifest.json",
            {
                "schema_version": 1,
                "tram_input_rows": 12,
                "files": {name: file_digest(root / name) for name in PREPARED_FILES},
            },
        )
    release = read(bundled / "manifest.json")
    manifest = read(rebuilt / "manifest.json")
    assert manifest["files"]["corpus.json.gz"] != release["files"]["corpus.json.gz"]
    check_rebuilt_preparation(rebuilt, bundled, release)

    archive = rebuilt / "corpus.json.gz"
    archive.write_bytes(gzip.compress(b'{"key":2}\n', mtime=12345))
    with pytest.raises(ValueError, match="prepared hash differs"):
        check_rebuilt_preparation(rebuilt, bundled, release)
    manifest["files"][archive.name] = file_digest(archive)
    write(rebuilt / "manifest.json", manifest)
    with pytest.raises(ValueError, match="prepared content differs"):
        check_rebuilt_preparation(rebuilt, bundled, release)

    archive.write_bytes((bundled / archive.name).read_bytes())
    manifest["files"][archive.name] = file_digest(archive)
    manifest["tram_input_rows"] += 1
    write(rebuilt / "manifest.json", manifest)
    with pytest.raises(ValueError, match="prepared manifest metadata"):
        check_rebuilt_preparation(rebuilt, bundled, release)
