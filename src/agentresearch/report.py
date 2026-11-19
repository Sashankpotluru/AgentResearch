"""Replay and export a recorded investigation without invoking a model."""

import html
import re

from .engine import append_event, new_run, next_action
from .retrieval import BM25Index
from .schema import RunConfig, canonical_bytes, integer, keys, string
from .tools import validate_output


def validate_run(run, corpus, *, index=None):
    """Check scheduling, chain, retrieval, spans, stored scores and derived decisions."""
    try:
        _validate(run, corpus, index)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("invalid trace structure") from exc


def _validate(run, corpus, index):
    integer(run["schema_version"], 1, 1)
    config = RunConfig.from_dict(run["config"])
    if (
        run["model_digest"] != config.model_digest
        or run["corpus_digest"] != corpus.digest
    ):
        raise ValueError("run binding mismatch")
    replay = new_run(string(run["claim"]), config, corpus.digest, string(run["run_id"]))
    keys(run, replay)
    if (
        type(run["events"]) is not list
        or len(run["events"]) > (2 + 2 * config.budget) * 3
    ):
        raise ValueError("invalid event count")
    if index is None:
        index = BM25Index(corpus)
    elif index.corpus.digest != corpus.digest:
        raise ValueError("index corpus mismatch")
    for event in run["events"]:
        if replay["status"] != "running":
            raise ValueError("event after terminal state")
        tool, args, attempt = next_action(replay)
        out, error = event["output"], event["error"]
        if error is None:
            validate_output(tool, args, out, index, config.model_digest)
        elif (
            error not in ("transient", "permanent", "invalid_output") or out is not None
        ):
            raise ValueError("invalid error event")
        append_event(replay, tool, args, attempt, out, error)
        if canonical_bytes(replay["events"][-1]) != canonical_bytes(event):
            raise ValueError(
                "event differs from expected tool, input, attempt or chain"
            )
    if canonical_bytes(run) != canonical_bytes(replay):
        raise ValueError("trace summary does not match replay")


def _md(value):
    # Escape Markdown punctuation first so generated HTML entities remain valid.
    escaped = re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", value)
    return html.escape(escaped, quote=False)


def _label(value):
    return _md(" ".join(value.splitlines()))


def to_markdown(run, corpus):
    validate_run(run, corpus)
    lines = [
        "# Claim investigation",
        "",
        _label(run["claim"]),
        "",
        f"Run {_label(run['run_id'])}",
        "",
    ]
    assessment = run["assessment"]
    if assessment is None:
        lines.append(f"**Incomplete investigation ({run['status']}).**")
    else:
        lines.extend(
            [
                f"**{assessment['final']['verdict']}**",
                "",
                f"Base assessment {assessment['base']['verdict']}.",
                "",
                "## Source withdrawal",
                "",
            ]
        )
        for sid, outcome in assessment["withdrawals"].items():
            lines.append(f"- Removing {_label(sid)} gives {outcome['verdict']}.")
    lines.extend(["", "## Evidence", ""])
    for r in run["scores"]:
        s = r["span"]
        source = corpus.by_id[s["source_id"]]
        lines.extend(
            [
                f"### {_label(source.title)}",
                "",
                (
                    f"Source {_label(source.source_id)}, sentence {_label(s['sentence_id'])}, "
                    f"characters [{s['start']}, {s['end']})."
                ),
                "",
            ]
        )
        lines.extend("> " + _md(line) for line in s["quote"].split("\n"))
        lines.extend(
            [
                "",
                (
                    f"Entailment {r['entailment']}, contradiction {r['contradiction']}, "
                    f"neutral {r['neutral']} (millionths)."
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation",
            "",
            (
                "An automated model assessed the quoted evidence within the supplied corpus. "
                "Its scores may be wrong. Source withdrawal measures dependence on the selected "
                "sources. The evidence can omit relevant studies."
            ),
        ]
    )
    return "\n".join(lines) + "\n"
