from concurrent.futures import ThreadPoolExecutor
from threading import Barrier


def test_concurrent_runs_share_one_committed_history(tmp_path):
    from test_engine import components, config

    from agentresearch.engine import Investigator
    from agentresearch.store import RunStore

    db = tmp_path / "runs.sqlite"
    barrier = Barrier(2)

    def worker():
        with RunStore(db) as store:
            inv = Investigator(components(), store)
            barrier.wait(timeout=10)
            return inv.run("ice", config(), "shared")

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: worker(), range(2)))
    assert a == b
    assert a["status"] == "complete"
    assert [e["sequence"] for e in a["events"]] == list(range(1, 11))
