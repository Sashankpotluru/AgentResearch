from pathlib import Path

import pytest


def components():
    from agentresearch.retrieval import BM25Index
    from agentresearch.schema import Corpus, ScoredSpan, load_json
    from agentresearch.tools import CorpusTools

    corpus = Corpus.from_dict(load_json(Path(__file__).parent / "fixtures/core.json"))

    class Verifier:
        model_digest = "fixture"

        def verify(self, claim, spans):
            return tuple(
                ScoredSpan(s, 10000, 975000, 15000, self.model_digest) for s in spans
            )

    return CorpusTools(corpus, BM25Index(corpus), Verifier())


def config(policy="challenge"):
    from agentresearch.schema import RunConfig

    return RunConfig(policy, 4, 700000, "fixture")


def test_policies_and_usage():
    from agentresearch.engine import Investigator
    from agentresearch.store import RunStore

    with RunStore(":memory:") as store:
        inv = Investigator(components(), store)
        first = inv.run("ice", config("first_hit"))
        assert first["usage"]["unique_sources"] == 1
        challenge = inv.run("ice", config())
        guard = inv.run("ice", config("guarded"))
        assert challenge["events"] == guard["events"]
        assert [e["tool"] for e in challenge["events"]] == [
            "search",
            "read",
            "verify",
            "read",
            "verify",
            "read",
            "verify",
            "search",
            "read",
            "verify",
        ]
        assert challenge["events"][7]["input"]["mode"] == "challenge"
        unique = [
            e["input"]["source_id"] for e in guard["events"] if e["tool"] == "read"
        ]
        assert len(set(unique)) == len(unique) == 4
        assert guard["usage"]["nli_pairs"] == 8
        short = inv.run("however", config())
        assert [e["tool"] for e in short["events"]] == [
            "search",
            "read",
            "verify",
            "search",
        ]
        miss = inv.run("unfindable", config())
        assert miss["status"] == "complete"
        assert miss["assessment"]["final"]["verdict"] == "INSUFFICIENT"
        assert miss["events"][-1]["input"]["mode"] == "focus"


def test_interruption_every_step_and_binding(tmp_path):
    from agentresearch.engine import Investigator
    from agentresearch.retrieval import BM25Index
    from agentresearch.schema import Corpus
    from agentresearch.store import RunStore
    from agentresearch.tools import CorpusTools

    with RunStore(":memory:") as s:
        reference = Investigator(components(), s).run("ice", config(), "x")
    for i in range(len(reference["events"]) + 1):
        db = tmp_path / str(i)
        with RunStore(db) as s:
            prefix = Investigator(components(), s).run(
                "ice", config(), "x", max_steps=i
            )
            assert prefix["events"] == reference["events"][:i]
        with RunStore(db) as s:
            inv = Investigator(components(), s)
            assert inv.resume("x") == reference
            with pytest.raises(ValueError):
                inv.run("ice", config("fixed"), "x")
            with pytest.raises(ValueError):
                inv.run("other", config(), "x")
            tools = components()
            smaller = Corpus(tools.corpus.sources[:1])
            with pytest.raises(ValueError):
                Investigator(
                    CorpusTools(smaller, BM25Index(smaller), tools.verifier), s
                ).resume("x")
            tools.verifier.model_digest = "changed"
            with pytest.raises(ValueError):
                Investigator(tools, s).resume("x")


@pytest.mark.parametrize("kind,attempts", [("transient", 3), ("permanent", 1)])
def test_provider_errors_bounded_and_redacted(kind, attempts):
    from agentresearch.engine import Investigator
    from agentresearch.store import RunStore
    from agentresearch.tools import TransientToolError

    tools = components()

    def fail(*args):
        error = TransientToolError if kind == "transient" else RuntimeError
        raise error("secret /private/model-path")

    tools.verifier.verify = fail
    with RunStore(":memory:") as s:
        run = Investigator(tools, s).run("ice", config())
    assert run["status"] == "failed" and run["assessment"] is None
    assert run["usage"]["attempts"]["verify"] == attempts
    assert run["usage"]["successes"]["verify"] == 0
    assert [e["attempt"] for e in run["events"] if e["error"]] == list(
        range(1, attempts + 1)
    )
    assert "secret" not in str(run)


def test_invalid_output_is_never_committed_as_evidence():
    from agentresearch.engine import Investigator
    from agentresearch.store import RunStore

    tools = components()
    tools.verifier.verify = lambda claim, spans: ()
    with RunStore(":memory:") as store:
        run = Investigator(tools, store).run("ice", config())
    assert run["status"] == "failed"
    assert run["events"][-1]["error"] == "invalid_output"
    assert run["scores"] == []


@pytest.mark.parametrize("tool", ["search", "read"])
def test_invalid_search_score_or_read_ranking_fails_before_commit(tool):
    from agentresearch.engine import Investigator
    from agentresearch.store import RunStore

    tools = components()
    if tool == "search":

        def forged_search(**args):
            hits = tools.index.search(**args)
            hits[0]["score"] += 1
            return hits

        tools.search = forged_search
    else:
        tools.read = lambda **args: tuple(reversed(tools.index.read(**args)))
    with RunStore(":memory:") as s:
        run = Investigator(tools, s).run("ice", config())
    assert run["status"] == "failed"
    assert run["events"][-1]["tool"] == tool
    assert run["events"][-1]["error"] == "invalid_output"
    assert run["events"][-1]["output"] is None


def test_resumes_retry_without_charging_failed_pairs(tmp_path):
    from agentresearch.engine import Investigator
    from agentresearch.store import RunStore
    from agentresearch.tools import TransientToolError

    tools = components()
    real_verify = tools.verifier.verify

    def fail(*args):
        raise TransientToolError

    tools.verifier.verify = fail
    with RunStore(tmp_path / "r") as s:
        prefix = Investigator(tools, s).run(
            "ice", config("first_hit"), "id", max_steps=3
        )
        assert prefix["events"][-1]["error"] == "transient"
        assert prefix["status"] == "running"
    tools.verifier.verify = real_verify
    with RunStore(tmp_path / "r") as s:
        result = Investigator(tools, s).resume("id")
    assert result["status"] == "complete"
    assert result["events"][-1]["attempt"] == 2
    assert result["usage"]["nli_pairs"] == 2
