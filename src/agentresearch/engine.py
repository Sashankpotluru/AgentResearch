"""Deterministic tool scheduling, bounded retries and resumable checkpoints."""

from .decision import decide, finalize
from .schema import RunConfig, ScoredSpan, Span, digest, integer, string
from .tools import TransientToolError, validate_output

TOOLS = ("search", "read", "verify")


def new_run(claim, config, corpus_digest, run_id):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "claim": claim,
        "config": config.to_dict(),
        "corpus_digest": corpus_digest,
        "model_digest": config.model_digest,
        "status": "running",
        "events": [],
        "scores": [],
        "assessment": None,
        "usage": {
            "attempts": dict.fromkeys(TOOLS, 0),
            "successes": dict.fromkeys(TOOLS, 0),
            "unique_sources": 0,
            "nli_pairs": 0,
        },
    }


def next_action(run):
    events = run["events"]
    claim = run["claim"]
    config = run["config"]
    if events and events[-1]["error"]:
        last = events[-1]
        return last["tool"], last["input"], last["attempt"] + 1

    def search(mode):
        return ("search", {"claim": claim, "mode": mode, "limit": 12}, 1)

    if not events:
        return search("relevance")
    last = events[-1]
    if last["tool"] == "read":
        return "verify", {"claim": claim, "spans": last["output"]}, 1

    completed = [e for e in events if e["error"] is None]
    searches = [e["output"] for e in completed if e["tool"] == "search"]
    read = {e["input"]["source_id"] for e in completed if e["tool"] == "read"}
    if len(read) >= config["budget"]:
        return None
    remaining = [
        hit["source_id"]
        for ranking in reversed(searches)
        for hit in ranking
        if hit["source_id"] not in read
    ]
    policy = config["policy"]
    pivot = (
        policy in ("challenge", "guarded")
        and len(searches) == 1
        and (len(read) >= 3 or not remaining)
    )
    if policy == "first_hit" or pivot:
        verdict = decide(
            (ScoredSpan.from_dict(s) for s in run["scores"]), config["threshold"]
        ).verdict
        if policy == "first_hit" and verdict != "INSUFFICIENT":
            return None
        if pivot:
            return search("focus" if verdict == "INSUFFICIENT" else "challenge")
    if remaining:
        return "read", {"claim": claim, "source_id": remaining[0]}, 1
    return None


def append_event(run, tool, args, attempt, output, error):
    events = run["events"]
    event = {
        "sequence": len(events) + 1,
        "tool": tool,
        "input": args,
        "output": output,
        "attempt": attempt,
        "error": error,
        "previous_hash": events[-1]["hash"] if events else None,
    }
    event["hash"] = digest(event)
    events.append(event)
    usage = run["usage"]
    usage["attempts"][tool] += 1
    if error is not None:
        if error != "transient" or attempt == 3:
            run["status"] = "failed"
        return
    usage["successes"][tool] += 1
    if tool == "read":
        usage["unique_sources"] += 1
    if tool == "verify":
        run["scores"].extend(output)
        usage["nli_pairs"] += len(output)
    if next_action(run) is None:
        run["status"] = "complete"
        run["assessment"] = finalize(
            (ScoredSpan.from_dict(s) for s in run["scores"]),
            RunConfig.from_dict(run["config"]),
        )


class Investigator:
    def __init__(self, tools, store):
        self.tools = tools
        self.store = store

    def run(self, claim, config, run_id=None, max_steps=None):
        string(claim)
        if (
            not isinstance(config, RunConfig)
            or config.model_digest != self.tools.verifier.model_digest
        ):
            raise ValueError("model/config mismatch")
        if max_steps is not None:
            integer(max_steps, 0)
        binding = new_run(claim, config, self.tools.corpus.digest, run_id)
        if run_id is None:
            run_id = digest([claim, config.to_dict(), self.tools.corpus.digest])
            binding["run_id"] = run_id
        string(run_id)
        with self.store.transaction():
            saved = self.store.get(run_id)
            if saved is None:
                self.store.save(binding)
            elif any(
                saved[k] != binding[k]
                for k in ("claim", "config", "corpus_digest", "model_digest")
            ):
                raise ValueError("stored run binding mismatch")
        return self.resume(run_id, max_steps)

    def resume(self, run_id, max_steps=None):
        if max_steps is not None:
            integer(max_steps, 0)
        steps = 0
        while True:
            with self.store.transaction():
                run = self.store.get(run_id)
                if run is None:
                    raise ValueError("unknown run ID")
                if (
                    run["corpus_digest"] != self.tools.corpus.digest
                    or run["model_digest"] != self.tools.verifier.model_digest
                    or run["config"]["model_digest"] != run["model_digest"]
                ):
                    raise ValueError("stored corpus/model binding mismatch")
                if run["status"] != "running" or steps == max_steps:
                    return run
                self._step(run)
                self.store.save(run)
            steps += 1

    def _step(self, run):
        tool, args, attempt = next_action(run)
        output = error = None
        call_args = (
            args
            if tool != "verify"
            else {
                "claim": args["claim"],
                "spans": tuple(Span.from_dict(s) for s in args["spans"]),
            }
        )
        try:
            raw = getattr(self.tools, tool)(**call_args)
        except TransientToolError:
            error = "transient"
        except Exception:  # noqa: BLE001 -- sanitize all external provider exceptions
            error = "permanent"
        else:
            try:
                if tool == "search":
                    payload = raw
                else:
                    expected = Span if tool == "read" else ScoredSpan
                    if type(raw) is not tuple or not all(
                        isinstance(item, expected) for item in raw
                    ):
                        raise ValueError("invalid tool return type")
                    payload = [item.to_dict() for item in raw]
                validate_output(
                    tool, args, payload, self.tools.index, run["model_digest"]
                )
                output = payload
            except (ValueError, KeyError, TypeError, AttributeError):
                error = "invalid_output"
        append_event(run, tool, args, attempt, output, error)
