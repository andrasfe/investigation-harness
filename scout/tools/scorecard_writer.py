"""finalize_scorecard — SPEC § 3.3. Terminates the evaluation.

Implements the pre-finalize review hook: when ESCALATE=1 the student
escalates the DRAFT scorecard + a verifier dry-run to the teacher before
committing. This is the V3 moment in the viability validation pipeline —
the teacher gets to semantically validate the student's judgment before
the run terminates. Teacher verdicts honoured:

    skip      → approve as-is, write the draft verbatim
    patch     → apply Resolution.fix as a field-path→value dict merged
                into the draft (teacher may correct specific fields)
    abort     → student raises StudentAbort; no scorecard written
    restart   → student raises StudentRestart; outer driver relaunches

If the channel is disabled the hook is a no-op and we write the draft
straight away (parity with pre-review behaviour).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from ..agent_context import AgentContext
from ..llm import ToolSpec
from ..models import Scorecard, compute_composite
from ..supervisor_channel import StudentAbort, StudentRestart


def _escalate_env_gate() -> bool:
    return os.environ.get("ESCALATE", "").strip().lower() in {"1", "true", "yes", "on"}

log = logging.getLogger(__name__)


class ScorecardFinalized(BaseException):
    """Signals the agent loop to exit after the student writes its final scorecard."""


def _pre_verify_summary(sc: Scorecard) -> dict[str, Any]:
    """Cheap self-verify the student runs before escalating.

    Mirrors the Layer-2 plausibility criteria that most often fire so the
    teacher sees the most likely rejection reasons without re-reading the
    full verifier source.
    """
    issues: list[str] = []
    if sc.recommendation.viable_target:
        if not sc.build.clean_build_succeeded:
            issues.append("viable_target=true but clean_build_succeeded=false")
        if not sc.tests.test_run_succeeded:
            issues.append("viable_target=true but test_run_succeeded=false")
        ve_count = len(sc.recommendation.viability_evidence or [])
        if ve_count < 3:
            issues.append(f"viable_target=true but viability_evidence has only {ve_count} items")
    if sc.tests.test_pass_rate < 0.0 or sc.tests.test_pass_rate > 1.0:
        issues.append(f"test_pass_rate={sc.tests.test_pass_rate} outside [0,1]")
    if sc.coverage.line_coverage_percent_overall > 0 and not sc.coverage.per_module_coverage:
        issues.append("line_coverage>0 but per_module_coverage is empty")
    return {"likely_rejection_reasons": issues}


def _apply_patch(draft: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a teacher patch into the draft scorecard.

    The patch is a flat field-path → value dict, e.g.
    ``{"build.clean_build_succeeded": true, "score.testability": 7}``.
    Unknown paths are ignored with a warning; non-dict intermediate nodes
    are left untouched (refuse to clobber unknown structure).
    """
    out = json.loads(json.dumps(draft))  # deep copy via serialization
    for path, value in (patch or {}).items():
        if not isinstance(path, str) or "." not in path:
            log.warning("scorecard patch: ignoring flat key %r (use dotted path)", path)
            continue
        parts = path.split(".")
        cursor = out
        for p in parts[:-1]:
            if p not in cursor or not isinstance(cursor[p], dict):
                log.warning("scorecard patch: ignoring %s (path not in draft)", path)
                cursor = None
                break
            cursor = cursor[p]
        if cursor is None:
            continue
        cursor[parts[-1]] = value
    return out


def _make_finalize(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
        scorecard_dict = dict(args.get("scorecard") or {})
        # Inject metadata from the context — we control these, not the LLM.
        scorecard_dict.setdefault("evaluation_id", ctx.config.evaluation_id)
        scorecard_dict.setdefault("repo_url", ctx.config.repo_url)
        md = dict(scorecard_dict.get("metadata") or {})
        md["student_version"] = ctx.config.student_version
        md["escalation_count"] = ctx.escalations_used
        md.setdefault("errors_encountered", list(ctx.errors))
        md.setdefault("evaluation_duration_seconds", 0)
        scorecard_dict["metadata"] = md

        try:
            draft_scorecard = Scorecard.from_dict(scorecard_dict)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"scorecard did not match schema: {exc}",
                "hint": "Check field names against SPEC.md §3.2.",
            }
        draft_scorecard.score.composite = compute_composite(draft_scorecard.score)

        # -- pre-finalize review escalation (V3) ------------------------------
        # Skips silently when the channel is disabled. Consumes one unit of
        # the escalation budget when it runs. The student MAY legitimately
        # hit budget-exhaustion here and fall through; that just means the
        # draft goes straight to disk and the verifier judges it alone.
        draft_path = ctx.config.run_dir / "scorecard.draft.json"
        draft_scorecard.write(draft_path)
        pre_verify = _pre_verify_summary(draft_scorecard)

        teacher_patch: dict[str, Any] = {}
        if ctx.channel.enabled and _escalate_env_gate() and ctx.escalations_used < ctx.config.escalation_budget:
            log.info("finalize: escalating pre_finalize_review to teacher")
            ctx.escalations_used += 1
            reso = ctx.channel.escalate(
                kind="end_of_cycle_review",
                summary=(
                    f"pre-finalize review for {ctx.config.repo_url}: "
                    f"viable_target={draft_scorecard.recommendation.viable_target}, "
                    f"likely_issues={len(pre_verify['likely_rejection_reasons'])}"
                ),
                context={
                    "phase": "pre_finalize_review",
                    "draft_scorecard": draft_scorecard.to_dict(),
                    "pre_verify": pre_verify,
                    "tool_calls_made": len(ctx.tool_trace),
                    "evidence_keys": sorted(ctx.evidence.keys()),
                },
                artifacts=[
                    str(draft_path),
                    str(ctx.trace_path()),
                    str(ctx.config.run_dir / "evidence.json"),
                ],
                student_hints=[
                    "Read scorecard.draft.json first; then consult evidence.json and agent_trace.jsonl.",
                    "Reply patch={<dotted.path>: <value>, ...} to correct specific fields;",
                    "  reply verdict=skip to approve as-is; abort if the scorecard is unsalvageable.",
                ],
                timeout_sec=float(args.get("review_timeout_sec", 300.0)),
            )
            if reso is None:
                log.warning("finalize: pre-review timed out — writing draft as-is")
            elif reso.verdict == "abort":
                raise StudentAbort(reso.notes or "teacher rejected at pre_finalize_review")
            elif reso.verdict == "restart":
                raise StudentRestart(reso.notes or "teacher requested restart at pre_finalize_review")
            elif reso.verdict == "patch":
                teacher_patch = dict(reso.fix or {})
                log.info("finalize: applying teacher patch to %d field(s)", len(teacher_patch))
            # skip / retry_with / unknown → treat as approve

        # -- apply teacher patch, rebuild scorecard --------------------------
        if teacher_patch:
            patched_dict = _apply_patch(draft_scorecard.to_dict(), teacher_patch)
            md = dict(patched_dict.get("metadata") or {})
            md["teacher_patched_fields"] = sorted(teacher_patch.keys())
            patched_dict["metadata"] = md
            final_scorecard = Scorecard.from_dict(patched_dict)
            final_scorecard.score.composite = compute_composite(final_scorecard.score)
        else:
            final_scorecard = draft_scorecard

        out_path = ctx.config.run_dir / "scorecard.json"
        final_scorecard.write(out_path)

        # Evidence map for the verifier. The tool trace is streamed to
        # agent_trace.jsonl by agent._execute_tool on every call — no bulk
        # flush here (would double-count every entry).
        (ctx.config.run_dir / "evidence.json").write_text(
            json.dumps(ctx.evidence, indent=2, default=str), encoding="utf-8"
        )
        ctx.scorecard_finalized = True
        raise ScorecardFinalized(str(out_path))

    return ToolSpec(
        name="finalize_scorecard",
        description=(
            "Write the final scorecard JSON and end the evaluation. "
            "Pass the complete scorecard as the `scorecard` argument following "
            "SPEC.md §3.2 schema. When the teacher channel is active the "
            "student escalates a pre-finalize review — the teacher may patch "
            "specific fields, approve (skip), or abort. Composite score is "
            "recomputed server-side; send 0.0 for it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "scorecard": {
                    "type": "object",
                    "description": "Full scorecard object, SPEC §3.2 shape.",
                },
                "review_timeout_sec": {
                    "type": "number",
                    "description": "How long to wait for the teacher's pre-finalize reply. Default 300.",
                },
            },
            "required": ["scorecard"],
        },
        fn=_fn,
    )


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802
    return [_make_finalize(ctx)]
