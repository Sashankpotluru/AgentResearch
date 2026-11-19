import pytest

from agentresearch.metrics import summarize


def records(gold, pred):
    return [
        {
            "query_id": str(i),
            "cluster_id": str(i // 2),
            "label": y,
            "prediction": p,
            "searches": 1,
            "reads": 1,
            "verifies": 1,
            "nli_pairs": 1,
            "pivotal": 0,
            "gold_sentences": 0,
            "found_sentences": 0,
            "eligible": 0,
            "complete": 0,
            "checked": 1,
            "intact": 1,
        }
        for i, (y, p) in enumerate(zip(gold, pred, strict=True))
    ]


def test_wrong_extra_classes_empty_class_and_selective_risk():
    rows = records([0, 0, 2, 2], [0, 3, 2, 4])
    rows[0].update(gold_sentences=2, found_sentences=1, eligible=1)
    result = summarize(rows, [0, 1, 2], draws=50)
    assert result["accuracy"] == 0.5
    assert result["macro_f1"] == pytest.approx(4 / 9)
    assert result["coverage"] == 0.75 and result["risk"] == pytest.approx(1 / 3)
    assert result["confusion"][0] == [1, 0, 0, 1, 0]
    assert result["per_class"]["1"]["recall"] is None
    assert result["sentence_recall"] == 0.5 and result["complete_rationale_rate"] == 0
    assert result["quote_integrity"] == 1


def test_risk_null_with_zero_coverage():
    result = summarize(records([2], [4]), [0, 1, 2], draws=3)
    assert result["coverage"] == 0 and result["risk"] is None
    assert result["intervals"]["component"]["risk"] == {
        "low": None,
        "high": None,
        "defined_draws": 0,
    }


def test_bootstrap_resamples_whole_groups_and_pairs_across_methods():
    from agentresearch.metrics import analyze

    rows = records([0] * 4, [0, 0, 1, 1])
    # Both records of a selected component must co-occur.
    r = summarize(rows, [0, 1, 2], draws=100)
    assert r["intervals"]["component"]["accuracy"] == {
        "low": 0.0,
        "high": 1.0,
        "defined_draws": 100,
    }
    source = [
        dict(row, policy=p, budget=b)
        for p in ("fixed", "challenge", "guarded", "first_hit")
        for b in (4, 8)
        for row in rows
    ]
    a = analyze(source, [0, 1, 2], draws=30, claim_sensitivity=True)
    for method in ("component", "claim"):
        assert a["contrasts"]["challenge-8_minus_fixed-8"]["intervals"][method][
            "accuracy"
        ] == {"low": 0.0, "high": 0.0, "defined_draws": 30}


def test_cluster_weights_preserve_every_claim_and_resample_multiplicity():
    from agentresearch.metrics import _draws

    rows = records([0] * 4, [0, 0, 0, 1])
    for i, r in enumerate(rows):
        r["cluster_id"] = "a" if i < 3 else "b"
    # Seed 1 takes [a,b], [b,b], [a,a]. The mixed draw weighs claims 3:1.
    observed = _draws({"only": rows}, [0, 1, 2], "component", 3, 1)["only"]["accuracy"]
    assert observed.tolist() == [0.75, 0.0, 1.0]
