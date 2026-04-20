"""Adversarial evaluation — Challenger + Judge.

Runs between the Proposer's `finalize_scorecard` and the final verifier
pass. The Challenger has read-only access to the same tools and tries to
refute specific claims in the draft scorecard. The Judge (deterministic;
LLM-backed variant in bead `vb2`) decides whether each challenge is
upheld, refuted, or needs teacher escalation.

File layout (added to the run directory):

    challenge.json                -- Challenger's raw output
    viability_challenge.json      -- Judge verdict + per-claim rulings
    viability_challenge_passed    -- 0-byte flag file iff judge.passed=true

The final verifier's Layer 2 checks `viability_challenge.passed` when
`recommendation.viable_target=true` — if the challenge ran and failed,
the scorecard is rejected. If the adversarial phase didn't run, the
verifier notes it but does not block (so `SCOUT_ADVERSARIAL=0` is safe).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .agent import run_agent
from .agent_context import AgentContext
from .config import ScoutConfig
from .llm import LLMClient

log = logging.getLogger(__name__)


@dataclass
class ChallengeRuling:
    field_path: str
    proposer_value: Any
    challenger_value: Any
    verdict: str         # "upheld" | "refuted" | "teacher_escalated" | "agreed"
    rationale: str
    confidence: str = "medium"
    evidence_tool: str = ""


@dataclass
class ViabilityChallengeResult:
    ran: bool
    challenger_model: str = ""
    challenge_count: int = 0
    rulings: list[ChallengeRuling] = field(default_factory=list)
    passed: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "challenger_model": self.challenger_model,
            "challenge_count": self.challenge_count,
            "rulings": [asdict(r) for r in self.rulings],
            "passed": self.passed,
            "notes": self.notes,
        }


def _initial_user_message(scorecard_path: Path, evidence_path: Path) -> str:
    try:
        scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"Failed to load draft scorecard: {exc}. Call finalize_challenge summary='draft_unreadable'."
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        evidence = {}

    # Trim to the fields most worth challenging to keep the prompt small.
    draft_compact = {
        "repo_url": scorecard.get("repo_url"),
        "build": scorecard.get("build", {}),
        "tests": scorecard.get("tests", {}),
        "coverage": scorecard.get("coverage", {}),
        "bug_history": scorecard.get("bug_history", {}),
        "maintainer_activity": scorecard.get("maintainer_activity", {}),
        "testability_signals": scorecard.get("testability_signals", {}),
        "score": scorecard.get("score", {}),
        "recommendation": scorecard.get("recommendation", {}),
    }
    return (
        "Here is the PROPOSER's draft scorecard (compacted). Your task is "
        "to find specific claims that you can REFUTE by re-running tools "
        "with different slices. File one challenge per material disagreement.\n\n"
        "DRAFT SCORECARD:\n"
        f"```json\n{json.dumps(draft_compact, indent=2, default=str)}\n```\n\n"
        "EVIDENCE MAP (tool → field):\n"
        f"```json\n{json.dumps(evidence, indent=2, default=str)}\n```\n\n"
        "Begin. Use static_analysis, git_log_analyze, github_api_query to "
        "re-verify claims. Call file_challenge for material disagreements. "
        "Call finalize_challenge when done (or after ~15 tool calls)."
    )


def run_challenger(
    *,
    ctx: AgentContext,
    client: LLMClient,
) -> Path | None:
    """Spawn the challenger agent against the Proposer's draft. Returns the
    `challenge.json` path, or None if no draft was available."""
    scorecard_path = ctx.config.run_dir / "scorecard.json"
    evidence_path = ctx.config.run_dir / "evidence.json"
    if not scorecard_path.exists():
        log.warning("adversarial: no scorecard.json — skipping challenger")
        return None

    user_msg = _initial_user_message(scorecard_path, evidence_path)
    run = run_agent(
        ctx=ctx,
        client=client,
        role="challenger",
        initial_user_message=user_msg,
        max_turns=25,
    )
    log.info(
        "adversarial: challenger halted reason=%s turns=%d calls=%d",
        run.halt_reason, run.turns, run.tool_calls_made,
    )
    out = ctx.config.run_dir / "challenge.json"
    if not out.exists():
        # Challenger never called finalize_challenge — synthesize an empty challenge.
        out.write_text(json.dumps({
            "challenger_version": ctx.config.student_version,
            "model": ctx.config.llm.model,
            "repo_url": ctx.config.repo_url,
            "evaluation_id": ctx.config.evaluation_id,
            "items": [],
            "summary": f"challenger_halted_without_finalize ({run.halt_reason})",
            "notes": "",
        }, indent=2), encoding="utf-8")
    return out


def _material_disagreement(proposer: Any, challenger: Any) -> bool:
    """Deterministic rule the Judge uses when the Challenger files a claim.

    Matches the criteria in prompts.CHALLENGER_INSTRUCTIONS: boolean flip,
    density bucket change, count off by >=25%, or evidence gap.
    """
    if proposer is None or challenger is None:
        # Missing evidence is always a material concern the judge forwards.
        return True
    if isinstance(proposer, bool) or isinstance(challenger, bool):
        return bool(proposer) != bool(challenger)
    if isinstance(proposer, str) and isinstance(challenger, str):
        return proposer.strip().lower() != challenger.strip().lower()
    try:
        p = float(proposer)
        c = float(challenger)
    except (TypeError, ValueError):
        return proposer != challenger
    if p == 0 and c == 0:
        return False
    denom = max(abs(p), abs(c), 1e-9)
    return abs(p - c) / denom >= 0.25


def run_judge(
    *,
    run_dir: Path,
    challenger_model: str = "",
) -> ViabilityChallengeResult:
    """Deterministic Judge: rule on each Challenger-filed claim.

    For every item, we classify:
      - agreed             (challenger's value matches proposer's — filed in error)
      - refuted            (material disagreement stands, claim is wrong)
      - teacher_escalated  (challenger flagged a missing evidence trail;
                            rule-based judge can't resolve without a human)
      - upheld             (material disagreement BUT low confidence + no tool cited
                            — we keep the claim but record the dispute)

    `passed` = True iff no `refuted` rulings.
    """
    challenge_path = run_dir / "challenge.json"
    if not challenge_path.exists():
        return ViabilityChallengeResult(ran=False, notes="no challenge.json")
    try:
        challenge = json.loads(challenge_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ViabilityChallengeResult(ran=False, notes=f"challenge.json unreadable: {exc}")

    items: list[dict[str, Any]] = challenge.get("items") or []
    rulings: list[ChallengeRuling] = []
    for item in items:
        p = item.get("proposer_value")
        c = item.get("challenger_value")
        if not _material_disagreement(p, c):
            verdict = "agreed"
        elif c is None:
            verdict = "teacher_escalated"
        elif item.get("confidence") == "high" and item.get("evidence_tool"):
            verdict = "refuted"
        elif item.get("evidence_tool"):
            # medium confidence with a cited tool — still refute unless the
            # disagreement is trivial (caught above by _material_disagreement).
            verdict = "refuted"
        else:
            # no evidence tool cited — the challenger filed a vibe. Uphold.
            verdict = "upheld"
        rulings.append(ChallengeRuling(
            field_path=str(item.get("field_path", "")),
            proposer_value=p,
            challenger_value=c,
            verdict=verdict,
            rationale=str(item.get("rationale", ""))[:500],
            confidence=str(item.get("confidence", "medium")).lower(),
            evidence_tool=str(item.get("evidence_tool", "")),
        ))

    refuted = any(r.verdict == "refuted" for r in rulings)
    result = ViabilityChallengeResult(
        ran=True,
        challenger_model=challenger_model or challenge.get("model", ""),
        challenge_count=len(items),
        rulings=rulings,
        passed=not refuted,
        notes=challenge.get("summary", ""),
    )

    # Persist verdict.
    (run_dir / "viability_challenge.json").write_text(
        json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    flag = run_dir / "viability_challenge_passed"
    if result.passed:
        flag.write_text(str(time.time()), encoding="utf-8")
    elif flag.exists():
        flag.unlink()
    return result


def is_enabled(config: ScoutConfig) -> bool:
    return os.environ.get("SCOUT_ADVERSARIAL", "").lower() in {"1", "true", "yes", "on"}


def run_adversarial_phase(
    *,
    ctx: AgentContext,
    client: LLMClient,
) -> ViabilityChallengeResult | None:
    """Full adversarial pass: Challenger → Judge. Caller must have ensured
    the Proposer already wrote scorecard.json + evidence.json."""
    try:
        run_challenger(ctx=ctx, client=client)
    except Exception as exc:  # noqa: BLE001
        log.warning("adversarial: challenger crashed: %s", exc)
        return ViabilityChallengeResult(ran=False, notes=f"challenger_crash: {exc}")
    return run_judge(run_dir=ctx.config.run_dir, challenger_model=ctx.config.llm.model)
