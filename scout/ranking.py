"""Ranking + selection-memo generation — SPEC § 9.2, § 9.3.

Pure functions. Given a set of verified scorecards, return the ranked list
and a markdown selection memo. Memo emission is deliberately deterministic
(no LLM call) — the memo is a summary of the structured data, which stays
auditable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Scorecard, compute_composite


DEFAULT_WEIGHTS = {
    "build_tractability": 0.25,
    "coverage_gap_value": 0.25,
    "testability": 0.20,
    "bug_history_richness": 0.15,
    "maintainer_responsiveness": 0.15,
}


@dataclass
class RankedCandidate:
    repo_url: str
    composite: float
    scorecard: Scorecard
    run_dir: Path
    accepted: bool
    rejection_reasons: list[str]


def load_verified_scorecards(batch_dir: Path) -> list[RankedCandidate]:
    candidates: list[RankedCandidate] = []
    for sub in sorted(batch_dir.iterdir()):
        sc_path = sub / "scorecard.json"
        vr_path = sub / "verifier_report.json"
        if not sc_path.exists():
            continue
        try:
            sc = Scorecard.from_dict(json.loads(sc_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        accepted = False
        rejection_reasons: list[str] = []
        if vr_path.exists():
            try:
                report = json.loads(vr_path.read_text(encoding="utf-8"))
                accepted = bool(report.get("accepted"))
                for layer in report.get("layers", []):
                    if not layer.get("ok"):
                        rejection_reasons.extend(
                            [f"{layer['name']}: {i}" for i in layer.get("issues") or []]
                        )
            except (OSError, json.JSONDecodeError):
                rejection_reasons.append("verifier_report unreadable")
        else:
            rejection_reasons.append("not verified")
        composite = compute_composite(sc.score, DEFAULT_WEIGHTS)
        candidates.append(
            RankedCandidate(
                repo_url=sc.repo_url,
                composite=composite,
                scorecard=sc,
                run_dir=sub,
                accepted=accepted,
                rejection_reasons=rejection_reasons,
            )
        )
    return candidates


def rank(candidates: list[RankedCandidate]) -> list[RankedCandidate]:
    # Only verifier-accepted candidates are eligible for selection;
    # unaccepted scorecards stay in the list but sort to the bottom.
    return sorted(
        candidates,
        key=lambda c: (c.accepted, c.scorecard.recommendation.viable_target, c.composite),
        reverse=True,
    )


def write_memo(ranked: list[RankedCandidate], out_path: Path, top_n: int = 5) -> None:
    lines: list[str] = []
    lines.append(f"# Scout — Selection Memo")
    lines.append("")
    lines.append(f"Generated: {datetime.utcnow().isoformat(timespec='seconds')}Z")
    lines.append(f"Candidates evaluated: {len(ranked)}")
    accepted = [c for c in ranked if c.accepted]
    viable = [c for c in accepted if c.scorecard.recommendation.viable_target]
    lines.append(f"Verifier-accepted: {len(accepted)}")
    lines.append(f"Viable targets: {len(viable)}")
    lines.append("")

    if not viable:
        lines.append("## No viable targets found")
        lines.append("")
        lines.append("Top unaccepted candidates (sorted by composite):")
        for c in ranked[:top_n]:
            lines.append(
                f"- {c.repo_url} — composite={c.composite:.2f}, "
                f"accepted={c.accepted}, reasons={c.rejection_reasons[:3]}"
            )
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return

    lines.append("## Top 5 candidates")
    lines.append("")
    lines.append("| Rank | Repo | Composite | Build | Cov% | Tests | Bugs24mo | Notes |")
    lines.append("|------|------|-----------|-------|------|-------|----------|-------|")
    for i, c in enumerate(ranked[:top_n], 1):
        sc = c.scorecard
        notes_cell = (sc.recommendation.notes or "").replace("|", "/")[:60]
        lines.append(
            f"| {i} | {c.repo_url} | {c.composite:.2f} | "
            f"{'ok' if sc.build.clean_build_succeeded else 'fail'} | "
            f"{sc.coverage.line_coverage_percent_overall:.1f} | "
            f"{sc.tests.test_count} ({sc.tests.test_pass_rate:.0%}) | "
            f"{sc.bug_history.bug_fix_commits_24mo} | {notes_cell} |"
        )
    lines.append("")

    primary = viable[0]
    backup = viable[1] if len(viable) > 1 else None

    lines.append("## Primary recommendation")
    lines.append("")
    lines.append(f"**{primary.repo_url}** (composite {primary.composite:.2f})")
    lines.append("")
    lines.append(f"- Recommended submodule: `{primary.scorecard.recommendation.recommended_submodule or '(root)'}`")
    lines.append(f"- Build: {primary.scorecard.build.build_system}, "
                 f"{primary.scorecard.build.clean_build_time_seconds}s")
    lines.append(f"- Line coverage: {primary.scorecard.coverage.line_coverage_percent_overall:.1f}%  "
                 f"(gap to 90%: {max(0.0, 90.0 - primary.scorecard.coverage.line_coverage_percent_overall):.1f} pts)")
    lines.append(f"- Bug-fix commits (24mo): {primary.scorecard.bug_history.bug_fix_commits_24mo}")
    lines.append(f"- Testability flags: refl={primary.scorecard.testability_signals.reflection_density}, "
                 f"static_state={primary.scorecard.testability_signals.static_state_density}, "
                 f"sleep_count={primary.scorecard.testability_signals.thread_sleep_count}")
    if primary.scorecard.recommendation.notes:
        lines.append(f"- Notes: {primary.scorecard.recommendation.notes}")
    lines.append("")

    if backup:
        lines.append("## Backup candidate")
        lines.append("")
        lines.append(f"**{backup.repo_url}** (composite {backup.composite:.2f})")
        lines.append("")

    lines.append("## Rationale")
    lines.append("")
    lines.append("Composite weights (SPEC § 9.2): "
                 + ", ".join(f"{k}={v}" for k, v in DEFAULT_WEIGHTS.items()))
    lines.append("")
    lines.append("Pilot runs on the top 2 candidates (SPEC § 9.4) have NOT yet run — "
                 "see the `scripts/run_pilot.sh` stub and the beads backlog for the "
                 "outstanding integration work before committing final selection.")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def generate_memo(batch_dir: Path, out_path: Path | None = None) -> Path:
    candidates = load_verified_scorecards(batch_dir)
    ranked = rank(candidates)
    target = out_path or (batch_dir / "selection_memo.md")
    write_memo(ranked, target)
    return target
