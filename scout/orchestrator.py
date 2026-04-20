"""Parallel multi-repo driver.

Default concurrency is 1 (`SCOUT_PARALLEL_REPOS=1`), matching the user's
"default to single agent" constraint. Concurrency >1 uses a thread pool —
repo evaluations are I/O + subprocess heavy and don't share mutable state
across evaluations, so threads are sufficient (no asyncio required and no
GIL contention on the heavy tools, which block in subprocess.run).

Each repo gets its own run_dir under `runs/<evaluation_id>/`, its own
supervisor channel, and its own LLM budget. The teacher session watches
the *parent* `runs/` directory for new escalations.jsonl files; `teach.sh`
(see scripts/) tails them all.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .config import RUNS_DIR
from .student import StudentResult, evaluate_repo

log = logging.getLogger(__name__)


@dataclass
class BatchRun:
    batch_id: str
    results: list[StudentResult]
    run_dir: Path
    duration_sec: int


def _iter_parse_list(path: Path) -> list[tuple[str, str]]:
    """Parse `initial-list.txt` — plain `name — url` lines, blank/header lines ignored."""
    out: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("tier"):
            continue
        # accept both '—' and '-' as separator
        for sep in (" — ", " -- ", " - "):
            if sep in line:
                name, _, url = line.partition(sep)
                name = name.strip()
                url = url.strip()
                if url.startswith("http"):
                    out.append((name, url))
                    break
    return out


def load_repo_list(path: Path) -> list[tuple[str, str]]:
    return _iter_parse_list(path)


def run_batch(
    *,
    repo_urls: Iterable[str],
    batch_id: str | None = None,
    parallel: int | None = None,
) -> BatchRun:
    repos = list(repo_urls)
    if not repos:
        raise ValueError("run_batch: empty repo list")
    batch_id = batch_id or f"batch-{int(time.time())}"
    batch_dir = RUNS_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    workers = parallel if parallel is not None else int(os.environ.get("SCOUT_PARALLEL_REPOS", "1"))
    workers = max(1, workers)

    log.info("batch: id=%s size=%d parallel=%d", batch_id, len(repos), workers)
    start = time.time()
    results: list[StudentResult] = []

    def _one(repo_url: str) -> StudentResult:
        eid = f"{batch_id}__{_slug(repo_url)}"
        run_dir = batch_dir / _slug(repo_url)
        try:
            res = evaluate_repo(
                repo_url=repo_url,
                evaluation_id=eid,
                run_dir_override=run_dir,
            )
            log.info("batch: done %s halt=%s duration=%ds", repo_url, res.halt_reason, res.duration_sec)
            return res
        except Exception as exc:  # noqa: BLE001
            log.exception("batch: evaluation crashed for %s", repo_url)
            return StudentResult(
                evaluation_id=eid,
                repo_url=repo_url,
                run_dir=run_dir,
                scorecard_path=None,
                halt_reason=f"crashed: {type(exc).__name__}: {exc}",
                duration_sec=0,
                escalations_used=0,
                swarm_mode=False,
                errors=[str(exc)],
            )

    if workers == 1:
        for url in repos:
            results.append(_one(url))
    else:
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            for res in pool.map(_one, repos):
                results.append(res)

    duration = int(time.time() - start)
    summary_path = batch_dir / "batch_summary.json"
    _write_summary(summary_path, batch_id, results, duration)
    return BatchRun(batch_id=batch_id, results=results, run_dir=batch_dir, duration_sec=duration)


def _write_summary(path: Path, batch_id: str, results: list[StudentResult], duration_sec: int) -> None:
    import json
    data = {
        "batch_id": batch_id,
        "duration_sec": duration_sec,
        "results": [
            {
                "evaluation_id": r.evaluation_id,
                "repo_url": r.repo_url,
                "run_dir": str(r.run_dir),
                "scorecard_path": str(r.scorecard_path) if r.scorecard_path else None,
                "halt_reason": r.halt_reason,
                "duration_sec": r.duration_sec,
                "escalations_used": r.escalations_used,
                "swarm_mode": r.swarm_mode,
                "errors": r.errors,
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _slug(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1].replace(".git", "")
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in tail)[:40]
