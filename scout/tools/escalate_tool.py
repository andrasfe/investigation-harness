"""escalate — SPEC § 3.3, § 3.4.

Synchronous escalation to the teacher (parallel Claude Code session) via
the supervisor channel. Honours SPEC § 3.5 — exceeds-budget terminates
the evaluation (returning a flag the agent must respect).
"""

from __future__ import annotations

import logging
import os
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


def _escalate_env_gate() -> bool:
    """Whether `ESCALATE=1` is set — the two-gate activation.

    When unset, `escalate()` returns a synthesised 'teacher not attached'
    result immediately instead of blocking for the channel timeout. The
    rules/facts/findings stores remain live regardless.
    """
    return os.environ.get("ESCALATE", "").strip().lower() in {"1", "true", "yes", "on"}


def _make_escalate(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
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

        # ── taught-rule short-circuit ──────────────────────────────────────
        # Before consuming an escalation slot or bothering the teacher,
        # check whether a previously-saved rule covers this case. A match
        # means "the teacher already taught us to skip this class of
        # problem"; apply the taught skip autonomously.
        if ctx.channel.rules_store is not None:
            try:
                ctx.channel.rules_store.reload()
            except Exception as exc:  # noqa: BLE001
                log.warning("rules_store.reload failed: %s", exc)
            msg = summary + " " + (context.get("error_msg") or "") + " " + (context.get("error") or "")
            rule = ctx.channel.rules_store.match(phase=kind, msg=msg)
            if rule is not None:
                log.info("escalate: taught-rule skip (kind=%s, reason=%s)", kind, rule.reason)
                return {
                    "ok": True,
                    "verdict": "skip",
                    "via_rule": True,
                    "rule_reason": rule.reason,
                    "notes": f"taught-skip rule applied: {rule.reason}",
                    "escalations_used": ctx.escalations_used,  # NOT incremented
                }

        if ctx.escalations_used >= ctx.config.escalation_budget:
            return {
                "ok": False,
                "budget_exceeded": True,
                "error": (
                    f"escalation budget of {ctx.config.escalation_budget} already consumed; "
                    "finalize the scorecard with viable_target=false and abandon further probes."
                ),
            }

        ctx.escalations_used += 1

        if not _escalate_env_gate() or not ctx.channel.enabled:
            # ESCALATE is not set — the student is running autonomously.
            # Return a synthetic 'teacher not attached' result; the caller
            # falls back to its default abandonment path (same as a timeout).
            return {
                "ok": True,
                "disabled": True,
                "verdict": "skip",
                "notes": "ESCALATE not set or channel disabled — fall back to default abandonment",
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
