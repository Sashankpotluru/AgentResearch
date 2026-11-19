"""Aggregate integer model judgments and recompute them under source withdrawal."""

from typing import NamedTuple

from .schema import Decision, ScoredSpan, integer


class _Witness(NamedTuple):
    # Natural tuple order gives the strongest score followed by stable identities.
    negative_score: int
    source_id: str
    sentence_id: str
    start: int
    end: int
    identity: str


def _summarize(scores, threshold):
    integer(threshold, 0, 1000000)
    seen = set()
    sides = ({}, {})
    for row in scores:
        if not isinstance(row, ScoredSpan):
            raise ValueError("expected scored span")  # noqa: TRY004 -- record validation error
        key = (row.span.source_id, row.span.sentence_id)
        if key in seen:
            raise ValueError("duplicate scored sentence")
        seen.add(key)
        for side, value in zip(sides, (row.entailment, row.contradiction), strict=True):
            if value >= threshold:
                span = row.span
                candidate = _Witness(
                    -value,
                    span.source_id,
                    span.sentence_id,
                    span.start,
                    span.end,
                    span.identity,
                )
                if key[0] not in side or candidate < side[key[0]]:
                    side[key[0]] = candidate
    return sides


def _decision(sides, omit=None):
    support, refute = (
        sorted(v for k, v in side.items() if k != omit) for side in sides
    )
    if support and refute:
        pairs = [(a, b) for a in support for b in refute if a.source_id != b.source_id]
        if pairs:
            a, b = min(
                pairs,
                key=lambda p: (p[0].negative_score + p[1].negative_score, p[0], p[1]),
            )
            return Decision("CONFLICT", (a.identity, b.identity), False)
        return Decision("INSUFFICIENT", (support[0].identity, refute[0].identity), True)
    if support:
        return Decision("SUPPORTS", (support[0].identity,), False)
    if refute:
        return Decision("REFUTES", (refute[0].identity,), False)
    return Decision("INSUFFICIENT", (), False)


def decide(scores, threshold):
    return _decision(_summarize(scores, threshold))


def withdrawal_audit(scores, threshold):
    rows = tuple(scores)
    sides = _summarize(rows, threshold)
    base = _decision(sides)
    withdrawals = {
        sid: _decision(sides, sid).to_dict()
        for sid in sorted({r.span.source_id for r in rows})
    }
    pivotal = [sid for sid, d in withdrawals.items() if d["verdict"] != base.verdict]
    return {
        "base": base.to_dict(),
        "withdrawals": withdrawals,
        "pivotal_sources": pivotal,
        "stable": not pivotal,
    }


def finalize(scores, config):
    rows = tuple(scores)
    if any(r.model_digest != config.model_digest for r in rows):
        raise ValueError("model identity mismatch")
    result = withdrawal_audit(rows, config.threshold)
    result["final"] = (
        Decision("ABSTAIN", (), False).to_dict()
        if config.policy == "guarded" and not result["stable"]
        else result["base"]
    )
    return result
