"""Command line for preparing corpora, investigating claims and replaying runs."""

import argparse
import json
import sqlite3
import sys
from contextlib import contextmanager
from functools import cache

from .data import acquire, pin_path, prepare, read_corpus
from .engine import Investigator
from .report import to_markdown, validate_run
from .retrieval import BM25Index
from .schema import POLICIES, RunConfig, load_json
from .store import RunStore
from .tools import CorpusTools


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def _inspect(args):
    run = load_json(args.run)
    corpus = read_corpus(args.corpus)
    if args.command == "report":
        sys.stdout.write(to_markdown(run, corpus))
    else:
        validate_run(run, corpus)
        _emit(
            {
                "schema_version": 1,
                "status": run["status"],
                "valid": True,
                "assessment": run["assessment"],
            }
        )


def _prepare(args):
    if args.download:
        acquire(args.raw_dir)
    _emit(prepare(args.raw_dir, args.out_dir))


def _pubmed(args):
    from .pubmed import fetch_corpus

    _emit(fetch_corpus(args.query, args.max_records, args.email).to_dict())


def _runtime(args):
    from .model import NeuralVerifier, ScoreCache

    corpus = read_corpus(args.corpus)
    index = BM25Index(corpus)

    @cache
    def network():
        return NeuralVerifier(
            corpus, args.model_dir, args.lock, args.device, args.batch_size
        )

    @contextmanager
    def investigator():
        with RunStore(args.db) as store:
            neural = network()
            if args.cache:
                with ScoreCache(args.cache, neural) as verifier:
                    yield Investigator(CorpusTools(corpus, index, verifier), store)
            else:
                yield Investigator(CorpusTools(corpus, index, neural), store)

    if args.command == "serve-mcp":
        from .mcp_server import create_server

        create_server(corpus, index, investigator, args.db).run(transport="stdio")
        return
    with investigator() as agent:
        if args.command == "run":
            config = RunConfig(
                args.policy,
                args.budget,
                args.threshold,
                agent.tools.verifier.model_digest,
            )
            result = agent.run(args.claim, config, args.run_id, args.max_steps)
        else:
            result = agent.resume(args.run_id, args.max_steps)
        _emit(result)
        if result["status"] == "failed":
            raise ValueError(
                "investigation failed; stdout contains its recorded tool errors"
            )


def build_parser():
    parser = argparse.ArgumentParser(prog="agentresearch")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in [
        ("audit", "Replay a trace against its corpus"),
        ("report", "Write a Markdown evidence report"),
    ]:
        command = commands.add_parser(name, help=description)
        command.add_argument("run", help="Run JSON path")
        command.add_argument("corpus", help="Corpus JSON or prepared corpus.jsonl")
        command.set_defaults(handler=_inspect)
    prep = commands.add_parser(
        "prepare",
        help="Check raw dataset pins and prepare corpora and evaluation splits",
    )
    prep.add_argument("--raw-dir", required=True)
    prep.add_argument("--out-dir", required=True)
    prep.add_argument(
        "--download",
        action="store_true",
        help="Acquire missing raw files from the pinned URLs",
    )
    prep.set_defaults(handler=_prepare)
    pubmed = commands.add_parser(
        "pubmed", help="Fetch PubMed abstracts for an explicit search query"
    )
    pubmed.add_argument("--query", required=True)
    pubmed.add_argument("--max-records", type=int, default=10)
    pubmed.add_argument("--email", help="Optional contact address sent to NCBI")
    pubmed.set_defaults(handler=_pubmed)
    for name, description in [
        ("run", "Investigate a claim"),
        ("resume", "Continue a saved investigation"),
        ("serve-mcp", "Serve the local corpus via MCP stdio"),
    ]:
        command = commands.add_parser(name, help=description)
        command.add_argument(
            "--corpus", required=True, help="Corpus JSON or prepared corpus.jsonl"
        )
        command.add_argument("--model-dir", required=True)
        command.add_argument("--lock", default=pin_path("model-lock.json"))
        command.add_argument("--db", required=True, help="SQLite checkpoint path")
        command.add_argument("--cache", help="Optional SQLite model score cache")
        command.add_argument("--device", choices=("cpu", "mps", "cuda"), default="cpu")
        command.add_argument("--batch-size", type=int, default=16)
        if name in ("run", "resume"):
            command.add_argument("--run-id", required=name == "resume")
            command.add_argument(
                "--max-steps",
                type=int,
                help="Stop after this many additional tool attempts",
            )
        if name == "run":
            command.add_argument("--claim", required=True)
            command.add_argument("--policy", choices=POLICIES, required=True)
            command.add_argument("--budget", type=int, required=True)
            command.add_argument(
                "--threshold", type=int, required=True, help="Integer millionths"
            )
        command.set_defaults(handler=_runtime)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (ValueError, OSError, sqlite3.Error, ImportError) as exc:
        parser.exit(2, f"agentresearch: {exc}\n")
