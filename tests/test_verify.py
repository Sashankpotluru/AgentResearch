from copy import deepcopy

import pytest
from test_data import tiny  # noqa: F401 -- shared local fixture
from test_metrics import records

from agentresearch import verify
from agentresearch.metrics import analyze


def test_independent_interval_parity_with_unbalanced_clusters():
    data = records([0, 0, 2, 1, 0, 0], [0, 4, 2, 4, 3, 0])
    for i, r in enumerate(data):
        r["cluster_id"] = "giant" if i < 4 else r["query_id"]
    rows = [
        dict(r, policy=p, budget=b)
        for p in ("fixed", "challenge", "guarded", "first_hit")
        for b in (4, 8)
        for r in data
    ]
    for r in rows:
        if r["policy"] == "first_hit":
            r["prediction"] = 4
    a = analyze(rows, [0, 1, 2], draws=45, claim_sensitivity=True)
    verify.check_statistics(a, rows, [0, 1, 2], 45, 17, True)
    corrupted = deepcopy(a)
    corrupted["configs"]["fixed-8"]["intervals"]["claim"]["macro_f1"]["low"] += 0.01
    with pytest.raises(ValueError):
        verify.check_statistics(corrupted, rows, [0, 1, 2], 45, 17, True)


def test_independent_verdict_and_counterfactuals():
    scores = [
        [1, 0, 2, 5000, 990000, 5000],
        [1, 1, 2, 990000, 5000, 5000],
        [2, 0, 2, 10000, 980000, 10000],
    ]
    assert verify.decision(scores, 700000) == 3
    assert verify.decision(scores, 700000, omit=1) == 0
    assert verify.decision(scores, 700000, omit=2) == 2
    assert verify.decision([], 700000) == 2


@pytest.fixture
def fixture_release(tmp_path, monkeypatch, tiny):  # noqa: F811 -- pytest injects shared fixture
    """Use the real adapter, cache, engine, writers, loaders, and both auditors."""
    import json

    from agentresearch import data, model
    from agentresearch import experiment as exp
    from agentresearch.schema import ScoredSpan, digest

    raw, prepared = tiny
    climate = raw / "climate-fever.jsonl"
    climate_rows = [json.loads(x) for x in climate.read_text().splitlines()]
    for i, r in enumerate(climate_rows):
        r["claim_id"] = str(101 + i)
        r["claim"] = "Fiction " + str(i)
    climate.write_text("\n".join(map(json.dumps, climate_rows)))
    pins = data._source_pins()
    pins[climate.name].update(
        bytes=climate.stat().st_size, sha256=data.file_digest(climate)
    )
    original_scifact = data._scifact

    def with_searchable_claims(path):
        corpus, splits = original_scifact(path)
        for rows in splits.values():
            for q in rows:
                q["claim"] = "soil " + q["query_id"]
        return corpus, splits

    monkeypatch.setattr(data, "_scifact", with_searchable_claims)
    manifest = data.prepare(raw, prepared)
    p = exp.read(exp.protocol_path())
    p.update(sources=manifest["sources"], datasets=manifest["datasets"])
    p["evaluation"]["uncertainty"]["draws"] = 7
    ppath = tmp_path / "protocol.json"
    exp.write(ppath, p)
    monkeypatch.setattr(exp, "protocol_path", lambda: ppath)
    monkeypatch.setattr(verify, "protocol_path", lambda: ppath)
    for ds, split in (
        ("scifact", "calibration"),
        ("scifact", "dev"),
        ("climate", "test"),
    ):
        _, queries = data.load_dataset(prepared, ds, split)
        ids = sorted(
            [
                [q["query_id"], exp.CLASSES.index(q["label"])]
                + ([q["cluster_id"]] if split != "calibration" else [])
                for q in queries
            ]
        )
        monkeypatch.setitem(
            verify.IDENTITIES,
            "calibration" if split == "calibration" else ds,
            digest(ids),
        )

    class Neural:
        def __init__(self, corpus, model_dir, lock_path, device, batch_size):
            self.corpus = corpus
            self.metadata = {
                "lock": exp.read(lock_path),
                "device": device,
                "batch_size": batch_size,
                "labels": ["contradiction", "entailment", "neutral"],
                "max_tokens": 512,
                "dtype": "float32",
                "encoding": "pair-premise-first/right-only-first/offset-prefix-v1",
                "quantization": "largest-remainder-millionths-v1",
                "versions": dict.fromkeys(
                    ("torch", "transformers", "tokenizers", "safetensors"), "fixture"
                ),
            }
            self.model_digest = digest(self.metadata)
            self.scored_pairs = self.truncated_pairs = 0

        def verify_many(self, pairs):
            result = []
            for _, s in pairs:
                result.append(ScoredSpan(s, 8000, 920000, 72000, self.model_digest))
            self.scored_pairs += len(result)
            return result

    monkeypatch.setattr(model, "NeuralVerifier", Neural)
    cache = tmp_path / "scores.sqlite"
    selected = tmp_path / "calibration.json"
    run = exp.calibrate(prepared, tmp_path, cache, selected)
    assert run["threshold"] == 900000  # all four grid values tie
    out = tmp_path / "results"
    work = tmp_path / "work"
    site = tmp_path / "site/data"
    exp.evaluate(prepared, tmp_path, cache, selected, out, work_dir=work, site_dir=site)
    return out, prepared, site, selected, cache, work


def test_fixture_release_complete_resume_and_local_climate(fixture_release, tmp_path):
    from agentresearch import experiment as exp

    out, prepared, site, selected, cache, work = fixture_release
    assert verify.verify_results(out, prepared, site_dir=site)["runs"] == {
        "scifact": 16,
        "climate": 16,
    }
    assert not verify.verify_results(out)["climate_source_replay"]
    summary = (out / "summary.json").read_bytes()
    exp.evaluate(prepared, tmp_path, cache, selected, out, work_dir=work, site_dir=site)
    assert (out / "summary.json").read_bytes() == summary
    assert all(
        v["physical_pairs"] == 0
        for v in exp.read(out / "physical.json")["invocation"].values()
    )


@pytest.mark.parametrize(
    "fault",
    [
        "score",
        "tracehash",
        "missing",
        "span",
        "budget",
        "model",
        "threshold",
        "aggregate",
        "site",
        "private",
    ],
)
def test_corruption_rejected_even_after_rehash(fixture_release, fault):
    from agentresearch import experiment as exp
    from agentresearch.data import file_digest

    out, prepared, site, *_ = fixture_release
    if fault in ("score", "missing", "budget", "private"):
        path = out / "climate/records.json.gz"
        v = exp.read(path)
        r = v["records"][0]
        if fault == "score":
            r["scores"][0][4] -= 1
            r["scores"][0][5] += 1
        elif fault == "missing":
            v["records"].pop()
        elif fault == "private":
            r["claim"] = "forbidden private field"
        else:
            r["budget"] = 3
    elif fault in ("tracehash", "span"):
        path = out / "scifact/traces/3.json.gz"
        v = exp.read(path)
        run = v["runs"]["fixed-4"]
        if fault == "tracehash":
            run["events"][0]["hash"] = "0" * 64
        else:
            run["scores"][0]["span"]["quote"] += " forged"
    elif fault in ("model", "threshold"):
        path = out / "calibration.json"
        v = exp.read(path)
        if fault == "model":
            v["model"]["lock"]["revision"] = "incorrect"
        else:
            v["threshold"] = 600000
    elif fault == "aggregate":
        path = out / "summary.json"
        v = exp.read(path)
        v["datasets"]["climate"]["configs"]["fixed-8"]["accuracy"] += 0.25
        exp.write_bytes(out / "summary.csv", exp.csv_bytes(v))
    else:
        path = site / "index.json"
        v = exp.read(path)
        v["claims"][0]["path"] = "wrong"
    exp.write(path, v)
    m = exp.read(out / "manifest.json")
    for rel in m["files"]:
        m["files"][rel] = file_digest(out / rel)
    for rel in m["site_files"]:
        m["site_files"][rel] = file_digest(site / rel)
    m["calibration_sha256"] = m["files"]["calibration.json"]
    exp.write(out / "manifest.json", m)
    with pytest.raises(ValueError):
        verify.verify_results(out, prepared, site_dir=site)


def test_midrun_checkpoint_resume_and_fail_closed(
    fixture_release, tmp_path, monkeypatch
):
    from agentresearch import experiment as exp

    original_out, prepared, _, selected, cache, _ = fixture_release
    out, work, site = (
        tmp_path / "resume-results",
        tmp_path / "resume-work",
        tmp_path / "resume-site",
    )
    observed = []
    original = exp._query_runs

    def fail_next(name, q, *args):
        observed.append((name, q["query_id"]))
        if len(observed) == 2:
            raise RuntimeError("fixture interruption")
        return original(name, q, *args)

    with monkeypatch.context() as mp:
        mp.setattr(exp, "_query_runs", fail_next)
        with pytest.raises(RuntimeError, match="fixture interruption"):
            exp.evaluate(
                prepared, tmp_path, cache, selected, out, work_dir=work, site_dir=site
            )
    assert not (out / "manifest.json").exists()

    def record_resume(name, q, *args):
        assert (name, q["query_id"]) != observed[0]
        return original(name, q, *args)

    monkeypatch.setattr(exp, "_query_runs", record_resume)
    exp.evaluate(prepared, tmp_path, cache, selected, out, work_dir=work, site_dir=site)
    assert (out / "summary.json").read_bytes() == (
        original_out / "summary.json"
    ).read_bytes()
    assert verify.verify_results(out, prepared, site_dir=site)["verified"]


@pytest.mark.parametrize(
    "fault",
    [
        "selection",
        "ordinal",
        "order",
        "length",
        "missing_pair",
        "site_bool",
        "site_float",
    ],
)
def test_rehashed_semantic_substitutions(fixture_release, fault):
    from agentresearch import experiment as exp
    from agentresearch.data import file_digest

    out, prepared, site, selected, *_ = fixture_release
    m = exp.read(out / "manifest.json")
    if fault.startswith("site_"):
        path = next((site / "claims").glob("*.json"))
        obj = exp.read(path)
        run = obj["runs"]["fixed-4"]
        if fault == "site_bool":
            run["events"][0]["sequence"] = True
        else:
            run["config"]["budget"] = 4.0
        exp.write(path, obj)
        m["site_files"][str(path.relative_to(site))] = file_digest(path)
    else:
        path = out / "calibration.json"
        obj = exp.read(path)
        scores = obj["queries"][0]["scores"]
        if fault == "selection":
            scores[0][3] += 1
            scores[0][5] -= 1
        elif fault == "ordinal":
            scores[0][1] = 999999
        elif fault == "order":
            scores.reverse()
        elif fault == "length":
            scores[0][2] = 999999
        else:
            scores.pop()
        exp.write(path, obj)
        if fault != "selection":
            # Simulate a flawed selection already present in the frozen reference.
            selected.write_bytes(path.read_bytes())
        m["calibration_sha256"] = m["files"]["calibration.json"] = file_digest(path)
    exp.write(out / "manifest.json", m)
    with pytest.raises(ValueError):
        verify.verify_results(out, prepared, site_dir=site)


def test_swapped_intact_checkpoints_stop_before_release(fixture_release, tmp_path):
    from agentresearch import experiment as exp

    _, prepared, _, selected, cache, work = fixture_release
    a = next(work.glob("*/101.json.gz"))
    b = a.with_name("102.json.gz")
    a_bytes, b_bytes = a.read_bytes(), b.read_bytes()
    a.write_bytes(b_bytes)
    b.write_bytes(a_bytes)
    out = tmp_path / "new-results"
    with pytest.raises(ValueError, match="checkpoint"):
        exp.evaluate(
            prepared,
            tmp_path,
            cache,
            selected,
            out,
            work_dir=work,
            site_dir=tmp_path / "new-site",
        )
    assert not (out / "manifest.json").exists()


def test_process_death_before_atomic_replace_and_resume(
    fixture_release, tmp_path, monkeypatch
):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from agentresearch import experiment as exp

    _, prepared, _, selected, cache, work = fixture_release
    out, site = tmp_path / "crash-results", tmp_path / "crash-site"
    unrelated = work / "unrelated_file"
    unrelated.write_text("preserve")
    write_bytes = exp.write_bytes
    # Execute the actual public write, with evaluate's selected staging path,
    # in a fresh process which dies after the flush and before the rename.
    child_code = """
import os
import sys
from pathlib import Path
from agentresearch.experiment import write_bytes
Path.replace = lambda *args: os._exit(73)
write_bytes(Path(sys.argv[1]), sys.stdin.buffer.read(), staging=sys.argv[2] or None)
"""

    def interrupt_public_write(path, data, *, staging=None):
        if out not in Path(path).parents:
            return write_bytes(path, data, staging=staging)
        child = subprocess.run(
            [sys.executable, "-c", child_code, str(path), str(staging or "")],
            input=data,
            capture_output=True,
            timeout=15,
            check=False,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(exp.__file__).resolve().parents[1]),
            },
        )
        assert child.returncode == 73, child.stderr
        raise InterruptedError("public writer terminated")

    with monkeypatch.context() as mp:
        mp.setattr(exp, "write_bytes", interrupt_public_write)
        with pytest.raises(InterruptedError, match="public writer terminated"):
            exp.evaluate(
                prepared, tmp_path, cache, selected, out, work_dir=work, site_dir=site
            )
    result = exp.evaluate(
        prepared, tmp_path, cache, selected, out, work_dir=work, site_dir=site
    )
    assert result["run_count"] == 32
    assert verify.verify_results(out, prepared, site_dir=site)["verified"]
    assert unrelated.read_text() == "preserve"


def test_explicit_external_selection_reference_and_digest(fixture_release, tmp_path):
    from agentresearch.data import file_digest

    out, _, _, selected, *_ = fixture_release
    independent = tmp_path / "previous-selection.json"
    independent.write_bytes(selected.read_bytes())
    assert verify.verify_results(out, frozen_calibration=independent)["verified"]
    assert verify.verify_results(out, frozen_sha256=file_digest(independent))[
        "verified"
    ]
    with pytest.raises(ValueError, match="separate"):
        verify.verify_results(out, frozen_calibration=out / "calibration.json")
    with pytest.raises(ValueError, match="independently frozen"):
        verify.verify_results(out, frozen_sha256="0" * 64)
