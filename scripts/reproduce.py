"""Run one frozen experiment stage. Commit calibration before invoking evaluate."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentresearch.cli import main

if __name__ == "__main__":
    if sys.argv[1:2] not in (["calibrate"], ["evaluate"], ["prepare"]):
        raise SystemExit(
            "usage: reproduce.py {prepare,calibrate,evaluate} [stage options]"
        )
    main()
