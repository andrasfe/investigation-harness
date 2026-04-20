"""Single-repo evaluation — the top of the student invocation per SPEC § 3.

`evaluate_repo(repo_url, evaluation_id, ...)` runs one agent (single or
swarm) against a single repository and returns the run result. The
parallel multi-repo driver in `orchestrator.py` calls this.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import run_agent
from .agent_context import AgentContext
from .config import ScoutConfig, build_config
from .llm import LLMClient, LLMError
from .supervisor_channel import StudentAbort, StudentRestart, SupervisorChannel
from .swarm import run_swarm

log = logging.getLogger(__name__)


@dataclass
class StudentResult:
    evaluation_id: str
    repo_url: str
    run_dir: Path
    scorecard_path: Path | None
    halt_reason: str
    duration_sec: int
    escalations_used: int
    swarm_mode: bool
    errors: list[str]


def _init_channel(config: ScoutConfig) -> SupervisorChannel:
    # The student always constructs a channel pointing at its run_dir, so
    # heartbeats + durable stores are populated even when ESCALATE is unset.
    # SupervisorChannel.escalate() is disabled internally unless the opt-in
    # env var is set (see .from_env); we bypass that here deliberately so a
    # parallel-repo driver can set env once and each repo's channel still
    # points at the right directory.
    import os
    opt_in = os.environ.get("ESCALATE", "").lower() in {"1", "true", "yes", "on"}
    return SupervisorChannel(config.run_dir if opt_in else None)


def evaluate_repo(
    *,
    repo_url: str,
    evaluation_id: str | None = None,
    target_modules: list[str] | None = None,
    run_dir_override: Path | None = None,
) -> StudentResult:
    """Evaluate a single Java repository end-to-end.

    No context hand-off between repos — each call is independent so the
    multi-repo driver can parallelise freely.
    """
    eid = evaluation_id or f"scout-{int(time.time())}-{_slug(repo_url)}"
    config = build_config(
        evaluation_id=eid,
        repo_url=repo_url,
        target_modules=target_modules,
        run_dir_override=run_dir_override,
    )
    _configure_logging(config.run_dir)

    log.info("student: start eid=%s url=%s swarm=%d", eid, repo_url, config.swarm_size)
    channel = _init_channel(config)
    channel.heartbeat({"phase": "start", "repo": repo_url, "swarm_size": config.swarm_size})
    ctx = AgentContext(config=config, channel=channel)

    if not config.llm.is_configured():
        raise LLMError(
            "LLM not configured — set OPENROUTER_API_KEY or OPENAI_API_KEY in .env"
        )
    client = LLMClient(
        api_key=config.llm.api_key,
        model=config.llm.model,
        base_url=config.llm.base_url,
        app_name="scout-student",
    )

    start = time.time()
    halt_reason = "ok"
    scorecard_path: Path | None = None
    try:
        if config.swarm_size <= 1:
            run = run_agent(ctx=ctx, client=client, role="full")
            scorecard_path = run.scorecard_path
            halt_reason = run.halt_reason
            channel.heartbeat({"phase": "finished_single", "halt": halt_reason, "turns": run.turns})
        else:
            run_swarm(ctx=ctx, client=client)
            scorecard_path = config.run_dir / "scorecard.json"
            halt_reason = "swarm_merged"
            channel.heartbeat({"phase": "finished_swarm", "halt": halt_reason})
    except StudentAbort as exc:
        halt_reason = f"teacher_abort: {exc}"
    except StudentRestart as exc:
        halt_reason = f"teacher_restart: {exc}"
        raise
    except LLMError as exc:
        halt_reason = f"llm_error: {exc}"
        ctx.errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("student: unhandled error")
        halt_reason = f"error: {type(exc).__name__}: {exc}"
        ctx.errors.append(str(exc))
    finally:
        duration = int(time.time() - start)
        # Stamp duration back into scorecard metadata if we have one on disk.
        sc_path = config.run_dir / "scorecard.json"
        if sc_path.exists():
            try:
                data = json.loads(sc_path.read_text(encoding="utf-8"))
                data.setdefault("metadata", {})["evaluation_duration_seconds"] = duration
                data["metadata"]["errors_encountered"] = list(
                    dict.fromkeys((data["metadata"].get("errors_encountered") or []) + ctx.errors)
                )
                sc_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("student: failed to stamp duration: %s", exc)

    return StudentResult(
        evaluation_id=eid,
        repo_url=repo_url,
        run_dir=config.run_dir,
        scorecard_path=scorecard_path,
        halt_reason=halt_reason,
        duration_sec=int(time.time() - start),
        escalations_used=ctx.escalations_used,
        swarm_mode=config.swarm_size > 1,
        errors=list(ctx.errors),
    )


# --------- utilities --------------------------------------------------------


def _slug(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1].replace(".git", "")
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in tail)[:40]


def _configure_logging(run_dir: Path) -> None:
    log_path = run_dir / "agent.log"
    root = logging.getLogger()
    # Per-repo file handler; keep root level untouched so the caller's
    # logging config persists.
    if any(isinstance(h, logging.FileHandler) and getattr(h, "_scout_tag", None) == str(log_path)
           for h in root.handlers):
        return
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler._scout_tag = str(log_path)  # type: ignore[attr-defined]
    handler.setLevel(logging.DEBUG)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
