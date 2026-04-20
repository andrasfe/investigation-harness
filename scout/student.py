"""Single-repo evaluation — the top of the student invocation per SPEC § 3.

`evaluate_repo(repo_url, evaluation_id, ...)` runs one agent (single or
swarm) against a single repository and returns the run result. The
parallel multi-repo driver in `orchestrator.py` calls this.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adversarial import is_enabled as adversarial_is_enabled
from .adversarial import run_adversarial_phase
from .agent import run_agent
from .agent_context import AgentContext
from .config import REPO_ROOT, ScoutConfig, build_config
from .llm import LLMClient, LLMError
from .supervisor_channel import StudentAbort, StudentRestart, SupervisorChannel
from .swarm import run_swarm
from .verifier import verify

PERSISTENT_KNOWLEDGE_DIR = REPO_ROOT / "state" / "knowledge"
KNOWLEDGE_FILES = ("teacher_rules.jsonl", "teacher_facts.jsonl", "teacher_findings.jsonl")


def _seed_knowledge(run_dir: Path) -> dict[str, int]:
    """Copy persistent teacher knowledge into this run's channel dir.

    The supervisor channel reads teacher_*.jsonl from `run_dir`. To make
    knowledge *stick across rounds*, we snapshot the committed
    `state/knowledge/teacher_*.jsonl` into the fresh run directory before
    the channel initialises. Without this, every run starts with empty
    rules/facts/findings regardless of what the teacher taught in prior runs.
    """
    stats: dict[str, int] = {"seeded_files": 0, "seeded_lines": 0}
    if not PERSISTENT_KNOWLEDGE_DIR.exists():
        return stats
    for name in KNOWLEDGE_FILES:
        src = PERSISTENT_KNOWLEDGE_DIR / name
        if not src.exists() or src.stat().st_size == 0:
            continue
        dst = run_dir / name
        if dst.exists() and dst.stat().st_size > 0:
            continue  # run already has its own copy (idempotent)
        try:
            data = src.read_bytes()
            dst.write_bytes(data)
            stats["seeded_files"] += 1
            stats["seeded_lines"] += data.count(b"\n")
        except OSError as exc:
            log.warning("knowledge seed: failed for %s: %s", name, exc)
    return stats

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


def _post_verifier_review(ctx: AgentContext, report, sc_path: Path) -> None:
    """Escalate a verifier rejection back to the teacher.

    Runs OUTSIDE the agent loop: the student LLM has already exited. The
    teacher's reply is applied to the scorecard on disk directly (no LLM
    round-trip). Verdicts:
        patch    → merge Resolution.fix into the scorecard, re-verify once
        skip     → leave the rejection in place (record as post_verifier_acknowledged)
        abort    → delete the scorecard (escalation will fail the evaluation)
        restart  → flag the scorecard with post_verifier_requested_rerun (student
                   driver doesn't retry itself; outer loop should)
    """
    rejected_issues: list[str] = []
    for layer in report.layers:
        if not layer.ok:
            rejected_issues.extend([f"{layer.name}: {i}" for i in layer.issues or []])
    log.info("post_verifier_review: escalating %d rejection issue(s)", len(rejected_issues))
    reso = ctx.channel.escalate(
        kind="end_of_cycle_review",
        summary=(
            f"verifier REJECTED scorecard for {ctx.config.repo_url}: "
            f"{len(rejected_issues)} issue(s) across layers"
        ),
        context={
            "phase": "post_verifier_review",
            "layer_issues": rejected_issues,
            "scorecard_path": str(sc_path),
            "verifier_report": report.to_dict(),
        },
        artifacts=[str(sc_path), str(ctx.config.run_dir / "verifier_report.json")],
        student_hints=[
            "Reply verdict=patch with Resolution.fix to correct fields on disk; we will re-verify once.",
            "Reply verdict=skip to record acknowledgement without changes.",
            "Reply verdict=abort to delete the scorecard (evaluation fails cleanly).",
        ],
        timeout_sec=600.0,
    )
    if reso is None:
        log.warning("post_verifier_review: no reply before timeout — leaving rejection in place")
        return
    if reso.verdict == "abort":
        try:
            sc_path.unlink()
        except OSError:
            pass
        ctx.errors.append(f"post_verifier_review_abort: {reso.notes}")
        return
    if reso.verdict == "restart":
        (ctx.config.run_dir / "post_verifier_requested_rerun").write_text(
            reso.notes or "restart", encoding="utf-8",
        )
        return
    if reso.verdict == "patch" and reso.fix:
        try:
            draft = json.loads(sc_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        # flat field-path → value patch (same shape as pre_finalize patch)
        for path, value in reso.fix.items():
            if not isinstance(path, str) or "." not in path:
                continue
            parts = path.split(".")
            cur = draft
            for p in parts[:-1]:
                if not isinstance(cur.get(p), dict):
                    cur = None
                    break
                cur = cur[p]
            if cur is None:
                continue
            cur[parts[-1]] = value
        md = draft.setdefault("metadata", {})
        md["teacher_patched_post_verifier"] = sorted(reso.fix.keys())
        sc_path.write_text(json.dumps(draft, indent=2, default=str), encoding="utf-8")
        try:
            verify(ctx.config.run_dir)  # re-verify once; writes updated verifier_report.json
        except Exception as exc:  # noqa: BLE001
            log.warning("post_verifier_review: re-verify failed: %s", exc)


def _init_channel(config: ScoutConfig) -> SupervisorChannel:
    # Always point the channel at the run_dir so rules_store, facts_store,
    # and findings_store are live for autonomous consultation. Whether
    # escalate() *actually* blocks waiting for a teacher is controlled by
    # ESCALATE=1 in escalate_tool.py — not by the channel itself. This
    # matches the teacher-student-loop guidance: a mature student runs
    # with SUPERVISOR_DIR set so rules/facts are honored, but without
    # ESCALATE so it doesn't round-trip for cases it can handle alone.
    return SupervisorChannel(config.run_dir)


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
    seed_stats = _seed_knowledge(config.run_dir)
    if seed_stats["seeded_files"]:
        log.info("student: seeded %d durable-knowledge file(s) (%d lines) from state/knowledge/",
                 seed_stats["seeded_files"], seed_stats["seeded_lines"])
    channel = _init_channel(config)
    channel.heartbeat({
        "phase": "start", "repo": repo_url, "swarm_size": config.swarm_size,
        "knowledge_seeded": seed_stats["seeded_files"],
    })
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

            # Adversarial phase (Challenger + Judge) — opt-in via
            # SCOUT_ADVERSARIAL=1. Runs between Proposer finalization and
            # the deterministic verifier so Layer 2.5 sees the judge's
            # ruling. Uses a fresh LLMClient (may be configured to a
            # different model via SCOUT_CHALLENGER_MODEL in future).
            if adversarial_is_enabled(config):
                channel.heartbeat({"phase": "adversarial_start"})
                try:
                    chall_model = os.environ.get("SCOUT_CHALLENGER_MODEL", config.llm.model)
                    chall_client = LLMClient(
                        api_key=config.llm.api_key,
                        model=chall_model,
                        base_url=config.llm.base_url,
                        app_name="scout-challenger",
                    )
                    vc = run_adversarial_phase(ctx=ctx, client=chall_client)
                    channel.heartbeat({
                        "phase": "adversarial_done",
                        "passed": bool(vc and vc.passed),
                        "challenges": (vc.challenge_count if vc else 0),
                    })
                except Exception as exc:  # noqa: BLE001
                    log.warning("student: adversarial phase crashed: %s", exc)
                    ctx.errors.append(f"adversarial_crash: {exc}")

            # V3/V2 — run verifier and emit a post_verifier_review escalation
            # if it rejects. The teacher has one last window to triage
            # (patch + re-verify, approve-as-invalid, or mark for rerun).
            try:
                report = verify(config.run_dir)
            except Exception as exc:  # noqa: BLE001
                log.warning("student: verifier crashed: %s", exc)
                report = None
            if (report is not None and not report.accepted and channel.enabled
                    and os.environ.get("ESCALATE", "").lower() in {"1", "true", "yes", "on"}):
                _post_verifier_review(ctx, report, sc_path)

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
