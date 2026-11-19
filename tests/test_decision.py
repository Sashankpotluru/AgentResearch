import pytest


def row(src, i, e, c):
    from agentresearch.schema import ScoredSpan, Span

    span = Span(src, str(i), i, i + 1, "x", "1" * 64)
    return ScoredSpan(span, c, e, 1000000 - c - e, "model")


def test_threshold_conflicts_and_deterministic_witnesses():
    from agentresearch.decision import decide

    a, b, c = row("a", 0, 700000, 0), row("a", 1, 0, 800000), row("b", 0, 800000, 0)
    assert decide([], 700000).verdict == "INSUFFICIENT"
    assert decide([a], 700000).verdict == "SUPPORTS"
    assert decide([b], 700000).verdict == "REFUTES"
    assert decide([a, b], 700000).internal_conflict
    assert decide([a, b], 700000).verdict == "INSUFFICIENT"
    d = decide([a, b, c], 700000)
    assert d.verdict == "CONFLICT"
    assert d.witnesses == (c.span.identity, b.span.identity)
    assert decide([c, b, a], 700000) == d
    with pytest.raises(ValueError):
        decide([a, a], 700000)


def test_withdraw_all_spans_from_source_and_guard():
    from agentresearch.decision import finalize
    from agentresearch.schema import RunConfig

    r = finalize(
        [row("a", 0, 800000, 0), row("a", 1, 750000, 0)],
        RunConfig("guarded", 4, 700000, "model"),
    )
    assert r["withdrawals"]["a"]["verdict"] == "INSUFFICIENT"
    assert r["pivotal_sources"] == ["a"]
    assert r["stable"] is False
    assert r["base"]["verdict"] == "SUPPORTS"
    assert r["final"]["verdict"] == "ABSTAIN"
