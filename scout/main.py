"""Scout CLI.

Subcommands:
    evaluate <repo_url>           Evaluate a single repository.
    batch <list_path>             Evaluate every repo in a list file.
    verify <run_dir>              Run the verifier against a finished run.
    rank <batch_dir>              Write a selection memo for a finished batch.

Examples:
    python -m scout.main evaluate https://github.com/apache/commons-imaging
    python -m scout.main batch initial-list.txt
    python -m scout.main verify runs/batch-17xxxxx/commons-imaging
    python -m scout.main rank runs/batch-17xxxxx
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .config import REPO_ROOT, RUNS_DIR
from .orchestrator import load_repo_list, run_batch
from .ranking import generate_memo
from .student import evaluate_repo
from .verifier import verify


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def cmd_evaluate(args: argparse.Namespace) -> int:
    result = evaluate_repo(
        repo_url=args.repo_url,
        evaluation_id=args.evaluation_id,
        target_modules=args.target_modules,
    )
    print(f"halt: {result.halt_reason}")
    print(f"run_dir: {result.run_dir}")
    if result.scorecard_path:
        print(f"scorecard: {result.scorecard_path}")
    return 0 if result.halt_reason in ("finalized", "swarm_merged", "ok") else 2


def cmd_batch(args: argparse.Namespace) -> int:
    path = Path(args.list_path)
    if not path.exists():
        print(f"error: list file not found: {path}", file=sys.stderr)
        return 2
    entries = load_repo_list(path)
    if args.limit:
        entries = entries[: args.limit]
    urls = [u for _, u in entries]
    print(f"batch: {len(urls)} repos, parallel={args.parallel}", file=sys.stderr)
    batch = run_batch(repo_urls=urls, batch_id=args.batch_id, parallel=args.parallel)
    print(f"batch_id: {batch.batch_id}")
    print(f"batch_dir: {batch.run_dir}")
    if args.auto_verify:
        for res in batch.results:
            if res.scorecard_path:
                verify(res.run_dir)
        memo = generate_memo(batch.run_dir)
        print(f"memo: {memo}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    report = verify(Path(args.run_dir))
    for layer in report.layers:
        status = "OK" if layer.ok else "FAIL"
        print(f"[{status}] {layer.name}")
        for issue in layer.issues:
            print(f"    - {issue}")
    print(f"accepted: {report.accepted}")
    return 0 if report.accepted else 2


def cmd_rank(args: argparse.Namespace) -> int:
    path = generate_memo(Path(args.batch_dir), out_path=Path(args.output) if args.output else None)
    print(f"memo: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scout", description="Java test-target investigation harness")
    p.add_argument("--verbose", "-v", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    ev = sub.add_parser("evaluate", help="Evaluate one repository")
    ev.add_argument("repo_url")
    ev.add_argument("--evaluation-id")
    ev.add_argument("--target-modules", nargs="*")
    ev.set_defaults(func=cmd_evaluate)

    bt = sub.add_parser("batch", help="Evaluate every repo in a list file")
    bt.add_argument("list_path")
    bt.add_argument("--batch-id")
    bt.add_argument("--parallel", type=int, default=None)
    bt.add_argument("--limit", type=int, default=0)
    bt.add_argument("--auto-verify", action="store_true")
    bt.set_defaults(func=cmd_batch)

    vf = sub.add_parser("verify", help="Run the verifier against one run dir")
    vf.add_argument("run_dir")
    vf.set_defaults(func=cmd_verify)

    rk = sub.add_parser("rank", help="Generate a selection memo for a batch dir")
    rk.add_argument("batch_dir")
    rk.add_argument("--output")
    rk.set_defaults(func=cmd_rank)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
