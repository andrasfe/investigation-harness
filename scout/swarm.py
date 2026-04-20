"""Specialist swarm + judge — opt-in alternative to single-agent mode.

Matches the agentic-student-architecture pattern 3 (specialist swarm with a
judge): K parallel LLM proposers with distinct focuses, then a cheap
deterministic+LLM judge merges. Default swarm_size=1 uses the single agent;
swarm_size>1 runs the specialists in threads (each with its own sub-run
directory to keep transcripts separable for Layer 3 verification).

Specialist roles (see prompts.py):
    build        — populates build + tests
    coverage     — populates coverage
    history      — populates bug_history + maintainer_activity
    testability  — populates testability_signals

A judge merges the drafts into a final scorecard. The judge can be
deterministic (preferred — matches the skill's "deterministic judge"
guidance) or LLM-backed for conflict resolution.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from .agent import AgentRun, run_agent
from .agent_context import AgentContext
from .config import ScoutConfig
from .llm import LLMClient
from .models import Scorecard, compute_composite
from .supervisor_channel import SupervisorChannel

log = logging.getLogger(__name__)

SPECIALIST_ORDER = ("build", "coverage", "history", "testability")


def _clone_ctx_for_role(base: AgentContext, role: str) -> AgentContext:
    """Give each specialist its own run_dir + channel so traces don't collide."""
    sub_dir = base.config.run_dir / f"specialist_{role}"
    sub_dir.mkdir(parents=True, exist_ok=True)
    sub_workspace = base.config.workspace_dir  # share the cloned checkout
    sub_config = replace(
        base.config,
        run_dir=sub_dir,
        workspace_dir=sub_workspace,
    )
    # Channel is tied to the run_dir; specialists get their own JSONL channel.
    sub_channel = SupervisorChannel(sub_dir if base.channel.enabled else None)
    ctx = AgentContext(config=sub_config, channel=sub_channel)
    ctx.repo_checkout = base.repo_checkout
    ctx.build_system_detected = base.build_system_detected
    return ctx


def _load_specialist_scorecard(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "scorecard.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("failed to load specialist scorecard at %s: %s", path, exc)
        return None


def _merge_scorecards(
    main: dict[str, Any], drafts: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Deterministic merge — specialist owns its fields, others stay from `main`.

    Field ownership (mirror of SPECIALIST_ROLES in prompts.py):
      build       -> build, tests
      coverage    -> coverage, score.coverage_gap_value
      history     -> bug_history, maintainer_activity,
                     score.bug_history_richness, score.maintainer_responsiveness
      testability -> testability_signals, score.testability
    """
    out = dict(main)
    errors = list((out.get("metadata") or {}).get("errors_encountered") or [])

    OWN = {
        "build":        [("build",), ("tests",), ("score", "build_tractability")],
        "coverage":     [("coverage",), ("score", "coverage_gap_value")],
        "history":      [("bug_history",), ("maintainer_activity",),
                         ("score", "bug_history_richness"),
                         ("score", "maintainer_responsiveness")],
        "testability":  [("testability_signals",), ("score", "testability")],
    }

    for role, draft in drafts.items():
        if not draft:
            errors.append(f"specialist[{role}] produced no scorecard")
            continue
        for path in OWN.get(role, []):
            src = draft
            for p in path:
                src = (src or {}).get(p) if isinstance(src, dict) else None
            if src is None:
                continue
            cursor = out
            for p in path[:-1]:
                cursor = cursor.setdefault(p, {})
            cursor[path[-1]] = src

    # Recompute composite from merged score block.
    score = out.setdefault("score", {})
    try:
        from .models import ScoreBlock
        sb = ScoreBlock(**{k: score.get(k, 0) for k in
                           ("build_tractability", "coverage_gap_value",
                            "testability", "bug_history_richness",
                            "maintainer_responsiveness")})
        score["composite"] = compute_composite(sb)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"composite recompute failed: {exc}")

    md = out.setdefault("metadata", {})
    md["errors_encountered"] = errors
    return out


def run_swarm(
    *,
    ctx: AgentContext,
    client: LLMClient,
) -> dict[str, Any]:
    """Run specialists in parallel and merge their drafts.

    Returns the merged scorecard dict (already written to disk as the
    evaluation's canonical `scorecard.json`). The full-agent mode should
    call `agent.run_agent(role='full')` directly instead of this.
    """
    size = max(1, int(ctx.config.swarm_size))
    if size <= 1:
        raise ValueError("run_swarm requires swarm_size >= 2; use run_agent for single-agent mode")

    selected = SPECIALIST_ORDER[:min(size, len(SPECIALIST_ORDER))]
    log.info("swarm: dispatching %d specialists: %s", len(selected), selected)

    drafts: dict[str, dict[str, Any]] = {}
    contexts: dict[str, AgentContext] = {role: _clone_ctx_for_role(ctx, role) for role in selected}

    def _run(role: str) -> tuple[str, AgentRun]:
        sub = contexts[role]
        run = run_agent(ctx=sub, client=client, role=role)
        return role, run

    with cf.ThreadPoolExecutor(max_workers=len(selected)) as pool:
        futures = [pool.submit(_run, r) for r in selected]
        for fut in cf.as_completed(futures):
            role, run = fut.result()
            drafts[role] = _load_specialist_scorecard(contexts[role].config.run_dir) or {}
            log.info(
                "swarm: specialist=%s halt=%s turns=%d calls=%d",
                role, run.halt_reason, run.turns, run.tool_calls_made,
            )

    # Skeleton scorecard — judge merges draft fields into this baseline.
    main: dict[str, Any] = {
        "evaluation_id": ctx.config.evaluation_id,
        "repo_url": ctx.config.repo_url,
        "metadata": {
            "student_version": ctx.config.student_version,
            "escalation_count": sum(c.escalations_used for c in contexts.values()),
            "errors_encountered": [],
        },
    }
    merged = _merge_scorecards(main, drafts)
    # Materialize a Scorecard dataclass to coerce defaults for missing blocks.
    sc = Scorecard.from_dict(merged)
    out_path = ctx.config.run_dir / "scorecard.json"
    sc.write(out_path)
    # Also aggregate evidence files from specialists for the verifier.
    agg_evidence: dict[str, dict[str, Any]] = {}
    for role, sub in contexts.items():
        for k, v in sub.evidence.items():
            agg_evidence[f"{role}:{k}"] = v
    (ctx.config.run_dir / "evidence.json").write_text(
        json.dumps(agg_evidence, indent=2, default=str), encoding="utf-8"
    )
    return sc.to_dict()
