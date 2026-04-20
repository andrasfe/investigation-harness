"""finalize_scorecard — SPEC § 3.3. Terminates the evaluation."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..agent_context import AgentContext
from ..llm import ToolSpec
from ..models import Scorecard, compute_composite

log = logging.getLogger(__name__)


class ScorecardFinalized(BaseException):
    """Signals the agent loop to exit after the student writes its final scorecard."""


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
            scorecard = Scorecard.from_dict(scorecard_dict)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"scorecard did not match schema: {exc}",
                "hint": "Check field names against SPEC.md §3.2.",
            }

        # Recompute composite to keep it consistent with the subscores.
        scorecard.score.composite = compute_composite(scorecard.score)

        out_path = ctx.config.run_dir / "scorecard.json"
        scorecard.write(out_path)

        # Evidence map for the verifier. The tool trace is streamed to
        # agent_trace.jsonl by agent._execute_tool on every call — no bulk
        # flush here (would double-count every entry).
        (ctx.config.run_dir / "evidence.json").write_text(
            json.dumps(ctx.evidence, indent=2, default=str), encoding="utf-8"
        )
        ctx.scorecard_finalized = True
        # Stop the loop — raise a BaseException so `except Exception` doesn't swallow it.
        raise ScorecardFinalized(str(out_path))

    return ToolSpec(
        name="finalize_scorecard",
        description=(
            "Write the final scorecard JSON and end the evaluation. "
            "Pass the complete scorecard as the `scorecard` argument following "
            "SPEC.md §3.2 schema. The composite score is recomputed server-side "
            "from your subscores — you may send 0.0 for it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "scorecard": {
                    "type": "object",
                    "description": "Full scorecard object, SPEC §3.2 shape.",
                },
            },
            "required": ["scorecard"],
        },
        fn=_fn,
    )


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802
    return [_make_finalize(ctx)]
