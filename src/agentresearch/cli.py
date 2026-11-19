"""Command-line entry points for inspecting runs."""

import argparse
import json
import sys

from .report import to_markdown, validate_run
from .schema import Corpus, load_json


def _inspect(args):
    run = load_json(args.run)
    corpus = Corpus.from_dict(load_json(args.corpus, max_bytes=256 * 1024 * 1024))
    if args.command == "report":
        sys.stdout.write(to_markdown(run, corpus))
    else:
        validate_run(run, corpus)
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": run["status"],
                    "valid": True,
                    "assessment": run["assessment"],
                },
                ensure_ascii=False,
            )
        )


def build_parser():
    parser = argparse.ArgumentParser(prog="agentresearch")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in [
        ("audit", "Replay a trace against its corpus"),
        ("report", "Write a Markdown evidence report to stdout"),
    ]:
        command = commands.add_parser(name, help=description)
        command.add_argument("run", help="Run JSON path")
        command.add_argument("corpus", help="Versioned corpus JSON path")
        command.set_defaults(handler=_inspect)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"agentresearch: {exc}\n")
