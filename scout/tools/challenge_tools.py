"""Tools exclusive to the Challenger role (adversarial evaluation).

The Challenger is a reviewer agent that inspects the Proposer's draft
scorecard and attempts to refute specific claims. It has access to the
read-only + re-run-able tools (static_analysis, git_log_analyze,
github_api_query) via the normal tool registry, plus these two tools to
record its verdict and terminate.

Deliberately NOT on the Challenger's surface:
    - run_build / run_tests / run_coverage  (the Proposer's deterministic
      artifacts are fixed; re-running them doubles cost without adding
      signal the Judge can act on).
    - escalate / finalize_scorecard / git_clone (Proposer-only).

The Challenger's output is a single JSON file `challenge.json`. The Judge
(`scout/adversarial.py::run_judge`) reads both `scorecard.json` and
`challenge.json` to produce `viability_challenge.json`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..agent_context import AgentContext
from ..llm import ToolSpec

log = logging.getLogger(__name__)


class ChallengeFinalized(BaseException):
    """Signals the challenger loop to exit after writing challenge.json."""


def _make_file_challenge(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
        required = ("field_path", "proposer_value", "challenger_value", "rationale")
        missing = [k for k in required if k not in args]
        if missing:
            return {"ok": False, "error": f"missing required args: {missing}"}

        entry = {
            "ts": time.time(),
            "field_path": str(args["field_path"]),
            "proposer_value": args["proposer_value"],
            "challenger_value": args["challenger_value"],
            "rationale": str(args["rationale"])[:600],
            "evidence_tool": str(args.get("evidence_tool", "")),
            "confidence": str(args.get("confidence", "medium")).lower(),
        }
        # Store on ctx so the Challenger's final step can flush them all at once.
        challenges = ctx.evidence.setdefault("__challenges__", {})
        challenges.setdefault("items", []).append(entry)
        return {
            "ok": True,
            "challenges_filed_so_far": len(challenges["items"]),
            "hint": "Call finalize_challenge once you've filed every disputed claim you can substantiate.",
        }

    return ToolSpec(
        name="file_challenge",
        description=(
            "Record a single disputed claim about the proposer's draft scorecard. "
            "Use this when a tool you re-ran returned a value that disagrees with "
            "what the proposer wrote, or when a proposer claim has no trace evidence. "
            "Required fields describe the specific scorecard path + the two values; "
            "rationale must cite the specific counter-observation (not vibes)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "field_path": {
                    "type": "string",
                    "description": "Dotted scorecard path, e.g. 'coverage.line_coverage_percent_overall'.",
                },
                "proposer_value": {
                    "description": "The value the proposer wrote for this field.",
                },
                "challenger_value": {
                    "description": "The value you observed re-running a tool (or null if you couldn't verify).",
                },
                "rationale": {
                    "type": "string",
                    "description": "One sentence: what tool you re-ran, what you saw, why it conflicts.",
                },
                "evidence_tool": {
                    "type": "string",
                    "description": "Name of the tool whose re-run produced the counter-observation.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                },
            },
            "required": ["field_path", "proposer_value", "challenger_value", "rationale"],
        },
        fn=_fn,
    )


def _make_finalize_challenge(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
        summary = str(args.get("summary", ""))[:500]
        notes = str(args.get("notes", ""))[:1000]
        store = ctx.evidence.get("__challenges__", {})
        items = store.get("items") or []

        payload = {
            "challenger_version": ctx.config.student_version,
            "model": ctx.config.llm.model,
            "repo_url": ctx.config.repo_url,
            "evaluation_id": ctx.config.evaluation_id,
            "items": items,
            "summary": summary,
            "notes": notes,
        }
        out_path = ctx.config.run_dir / "challenge.json"
        out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        raise ChallengeFinalized(str(out_path))

    return ToolSpec(
        name="finalize_challenge",
        description=(
            "Write challenge.json and end the challenger run. Call this exactly "
            "once after filing every dispute you can substantiate. You may file "
            "zero challenges if the draft looks defensible — just call this with "
            "summary='no_disputes_found'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "One line summary for the judge."},
                "notes": {"type": "string", "description": "Optional extra context."},
            },
            "required": ["summary"],
        },
        fn=_fn,
    )


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802
    return [_make_file_challenge(ctx), _make_finalize_challenge(ctx)]
