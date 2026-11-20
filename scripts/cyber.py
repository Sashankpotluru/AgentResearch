"""Prepare pinned threat-intelligence data, evaluate retrieval and model runs, or verify results."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentresearch.cyber_data import intake, prepare
from agentresearch.cyber_experiment import evaluate
from agentresearch.cyber_verify import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser(
        "prepare",
        help="Verify immutable input pins and prepare a private research corpus",
    )
    prep.add_argument("--raw", type=Path, default=Path(".artifacts/cyber-intake"))
    prep.add_argument(
        "--download",
        action="store_true",
        help="Fetch missing pinned files over verified HTTPS",
    )
    prep.add_argument(
        "--prepared", type=Path, default=Path(".artifacts/cyber-prepared")
    )
    exp = commands.add_parser(
        "evaluate", help="Run frozen lexical retrieval and real NLI investigations"
    )
    exp.add_argument("--model", type=Path, default=Path(".artifacts/model"))
    exp.add_argument("--work", type=Path, default=Path(".artifacts/cyber-runs"))
    exp.add_argument("--prepared", type=Path, default=Path(".artifacts/cyber-prepared"))
    exp.add_argument("--results", type=Path, default=Path("results-cyber"))
    exp.add_argument("--device", choices=("cpu", "cuda", "mps"), default="cpu")
    exp.add_argument("--batch-size", type=int, default=8)
    exp.add_argument("--site-dir", type=Path, default=Path(".artifacts/cyber-demo"))
    package = commands.add_parser(
        "package",
        help="Export existing measured traces and browser bundles without rescoring",
    )
    package.add_argument(
        "--prepared", type=Path, default=Path(".artifacts/cyber-prepared")
    )
    package.add_argument("--work", type=Path, default=Path(".artifacts/cyber-runs"))
    package.add_argument("--results", type=Path, default=Path("results-cyber"))
    package.add_argument("--site-dir", type=Path, default=Path(".artifacts/cyber-demo"))
    ver = commands.add_parser(
        "verify",
        help="Verify inventory, sample assignment and independently recomputed metrics",
    )
    ver.add_argument("--results", type=Path, default=Path("results-cyber"))
    ver.add_argument(
        "--site-dir", type=Path, help="Verify browser payloads against recorded runs"
    )
    inputs = ver.add_mutually_exclusive_group()
    inputs.add_argument(
        "--prepared",
        type=Path,
        help="Also replay every lexical ranking and all saved traces",
    )
    inputs.add_argument(
        "--strict-intake",
        type=Path,
        help="Derive fresh local preparation from verified original files and replay",
    )
    args = parser.parse_args()
    if args.command == "prepare":
        intake(args.raw, download=args.download)
        response = prepare(args.raw, args.prepared)
    elif args.command == "evaluate":
        response = evaluate(
            args.prepared,
            args.work,
            args.results,
            args.model,
            args.device,
            args.batch_size,
            site_dir=args.site_dir,
        )
    elif args.command == "package":
        from agentresearch.cyber_publication import publish

        response = publish(args.prepared, args.work, args.results, args.site_dir)
    else:
        response = verify(
            args.results,
            args.prepared,
            strict_intake=args.strict_intake,
            site_dir=args.site_dir,
        )
    print(json.dumps(response, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
