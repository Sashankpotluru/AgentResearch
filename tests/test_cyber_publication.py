"""The browser copy must remain bound to the separately verified research run."""

import copy

import pytest
from test_report import example

from agentresearch.cyber_publication import demo_record
from agentresearch.cyber_verify import verify_demo
from agentresearch.data import file_digest
from agentresearch.experiment import write


@pytest.mark.parametrize("changed", ["source", "score_quote", "summary"])
def test_resealed_browser_payload_cannot_change_evidence_or_results(tmp_path, changed):
    run, corpus = example()
    query = {
        "query_id": "case-1",
        "claim": run["claim"],
        "family": "Fixture family",
        "family_id": "S0001",
        "technique": "Fixture technique",
        "technique_id": "T0001",
        "ransomware_related": False,
    }
    catalog = {
        "queries": [query],
        "families": {
            "S0001": {"description": "A synthetic family used only in unit tests."}
        },
    }
    key = f"{run['config']['policy']}-{run['config']['budget']}"
    grouped = {"case-1": {key: run}}
    p = {
        "agent_threshold": run["config"]["threshold"],
        "agent_policies": [run["config"]["policy"]],
        "agent_budgets": [run["config"]["budget"]],
    }
    summary = {"model_digest": run["model_digest"], "measured": 1}
    selected = {"demo": ["case-1"]}
    index = {
        "schema_version": 1,
        "claims": [{**query, "path": "claims/case-1.json"}],
        "corpus_digest": corpus.digest,
        "model_digest": run["model_digest"],
        "threshold": p["agent_threshold"],
        "policies": p["agent_policies"],
        "budgets": p["agent_budgets"],
    }
    record = demo_record(query, grouped["case-1"], corpus, catalog)
    for path, obj in (
        ("index.json", index),
        ("summary.json", summary),
        ("claims/case-1.json", record),
    ):
        write(tmp_path / path, obj)

    def manifest():
        return {
            "site_files": {
                f.relative_to(tmp_path).as_posix(): file_digest(f)
                for f in tmp_path.rglob("*")
                if f.is_file()
            }
        }

    verify_demo(tmp_path, manifest(), summary, selected, catalog, corpus, grouped, p)
    if changed == "summary":
        write(tmp_path / "summary.json", {**summary, "measured": 99})
    else:
        edited = copy.deepcopy(record)
        if changed == "source":
            next(iter(edited["sources"].values()))["text"] += " changed"
        else:
            edited["runs"][key]["scores"][0]["span"]["quote"] += " changed"
        write(tmp_path / "claims/case-1.json", edited)
    with pytest.raises(ValueError, match="differs"):
        verify_demo(
            tmp_path, manifest(), summary, selected, catalog, corpus, grouped, p
        )
