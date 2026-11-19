"""Verify public results, demo exports, and optionally the reacquired Climate corpus."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentresearch.verify import verify_results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default="results")
    parser.add_argument("--site-dir", default="site/data")
    parser.add_argument("--prepared")
    reference = parser.add_mutually_exclusive_group()
    reference.add_argument("--frozen-calibration")
    reference.add_argument("--frozen-sha256")
    args = parser.parse_args()
    print(
        json.dumps(
            verify_results(
                args.results,
                args.prepared,
                site_dir=args.site_dir,
                frozen_calibration=args.frozen_calibration,
                frozen_sha256=args.frozen_sha256,
            )
        )
    )
