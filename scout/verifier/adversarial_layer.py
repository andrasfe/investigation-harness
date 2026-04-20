"""Verifier Layer 2.5 — adversarial challenge check.

If an adversarial phase ran for this evaluation (`viability_challenge.json`
exists next to the scorecard), this layer enforces:

    viable_target=true  →  viability_challenge.passed must be true

If the adversarial phase did NOT run, this layer is skipped silently
(`SCOUT_ADVERSARIAL=0` is the default; a harness running without
adversarial review is allowed, just noisier).

The layer also surfaces the per-claim rulings in `details` so the
teacher's review packet can reference them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import Scorecard


def check_adversarial(
    s: Scorecard, run_dir: Path
) -> tuple[bool, list[str], dict[str, Any]]:
    issues: list[str] = []
    details: dict[str, Any] = {"ran": False}

    vc_path = run_dir / "viability_challenge.json"
    if not vc_path.exists():
        return (True, issues, details)

    try:
        vc = json.loads(vc_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(f"viability_challenge.json unreadable: {exc}")
        return (False, issues, details)

    ran = bool(vc.get("ran"))
    passed = bool(vc.get("passed"))
    details.update(
        ran=ran,
        passed=passed,
        challenge_count=int(vc.get("challenge_count", 0)),
        refuted_fields=[r.get("field_path") for r in (vc.get("rulings") or [])
                        if r.get("verdict") == "refuted"],
        teacher_escalated_fields=[r.get("field_path") for r in (vc.get("rulings") or [])
                                  if r.get("verdict") == "teacher_escalated"],
    )

    if not ran:
        return (True, issues, details)

    if s.recommendation.viable_target and not passed:
        issues.append(
            f"viable_target=true but adversarial judge REFUTED "
            f"{len(details['refuted_fields'])} claim(s): {details['refuted_fields']}"
        )

    return (len(issues) == 0, issues, details)
