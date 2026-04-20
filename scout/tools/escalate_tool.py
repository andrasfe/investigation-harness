"""escalate — SPEC § 3.3, § 3.4.

Synchronous escalation to the teacher (parallel Claude Code session) via
the supervisor channel. Honours SPEC § 3.5 — exceeds-budget terminates
the evaluation (returning a flag the agent must respect).
"""

from __future__ import annotations

import logging
from typing import Any

from ..agent_context import AgentContext
from ..llm import ToolSpec

log = logging.getLogger(__name__)


VALID_KINDS = {
    "build_system",
    "coverage_tool",
    "bug_mining",
    "structure",
    "timeout",
    "other",
}


def _make_escalate(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
        if ctx.escalations_used >= ctx.config.escalation_budget:
            return {
                "ok": False,
                "budget_exceeded": True,
                "error": (
                    f"escalation budget of {ctx.config.escalation_budget} already consumed; "
                    "finalize the scorecard with viable_target=false and abandon further probes."
                ),
            }

        kind = str(args.get("kind", "other"))
        if kind not in VALID_KINDS:
            kind = "other"
        summary = str(args.get("summary", ""))[:400]
        context = dict(args.get("context") or {})
        context.setdefault("repo_url", ctx.config.repo_url)
        context.setdefault("evaluation_id", ctx.config.evaluation_id)
        context.setdefault("student_version", ctx.config.student_version)
        artifacts = list(args.get("artifacts") or [])
        hints = list(args.get("student_hints") or [])

        ctx.escalations_used += 1

        if not ctx.channel.enabled:
            # Mirror the disabled-channel contract: caller gets a no-op resolution
            # and must fall back to default abandonment.
            return {
                "ok": True,
                "disabled": True,
                "verdict": "skip",
                "notes": "channel disabled — fall back to default abandonment",
                "escalations_used": ctx.escalations_used,
            }

        resolution = ctx.channel.escalate(
            kind=kind,
            summary=summary,
            context=context,
            artifacts=artifacts,
            student_hints=hints,
        )

        if resolution is None:
            return {
                "ok": True,
                "timed_out": True,
                "verdict": "skip",
                "notes": "teacher did not reply before timeout — abandon probe",
                "escalations_used": ctx.escalations_used,
            }

        return {
            "ok": True,
            "verdict": resolution.verdict,
            "fix": resolution.fix,
            "notes": resolution.notes,
            "escalations_used": ctx.escalations_used,
        }

    return ToolSpec(
        name="escalate",
        description=(
            "Synchronously escalate to the teacher agent. Use ONLY when: a build "
            "system is unsupported; coverage extraction fails; bug-fix detection yields zero; "
            "repo structure doesn't fit; or an evaluation budget is about to be exceeded. "
            "Consumes one unit of the per-repo escalation budget."
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": sorted(VALID_KINDS)},
                "summary": {"type": "string", "description": "One-line human-readable reason."},
                "context": {"type": "object", "description": "Free-form payload for the teacher to diagnose from."},
                "artifacts": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Absolute file paths the teacher should Read (logs, reports).",
                },
                "student_hints": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["kind", "summary"],
        },
        fn=_fn,
    )


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802
    return [_make_escalate(ctx)]
