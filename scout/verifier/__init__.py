"""Verifier pipeline — SPEC § 5.

Layers implemented in this module:
    1. Schema validation
    2. Plausibility checks
    3. Trace verification
    (4. Sampled correctness — not implemented in this scaffold; see beads)
    (5. Canary regression — not implemented; see beads)

Verifier judgment is authoritative (SPEC § 5.2). `verify()` returns a
`VerifierReport` with per-layer results. A scorecard is accepted only when
every layer reports `ok=True`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import Scorecard
from .plausibility import check_plausibility
from .schema import check_schema
from .trace import check_trace

log = logging.getLogger(__name__)


@dataclass
class LayerResult:
    name: str
    ok: bool
    issues: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerifierReport:
    run_dir: Path
    scorecard_path: Path
    layers: list[LayerResult]
    accepted: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_dir": str(self.run_dir),
            "scorecard_path": str(self.scorecard_path),
            "accepted": self.accepted,
            "layers": [
                {"name": l.name, "ok": l.ok, "issues": l.issues, "details": l.details}
                for l in self.layers
            ],
        }

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")


def verify(run_dir: Path) -> VerifierReport:
    run_dir = Path(run_dir)
    scorecard_path = run_dir / "scorecard.json"
    if not scorecard_path.exists():
        return VerifierReport(
            run_dir=run_dir,
            scorecard_path=scorecard_path,
            layers=[LayerResult(name="preflight", ok=False,
                                issues=["scorecard.json missing"])],
            accepted=False,
        )

    try:
        raw = json.loads(scorecard_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return VerifierReport(
            run_dir=run_dir,
            scorecard_path=scorecard_path,
            layers=[LayerResult(name="preflight", ok=False,
                                issues=[f"scorecard.json not valid JSON: {exc}"])],
            accepted=False,
        )

    layers: list[LayerResult] = []

    schema_ok, schema_issues = check_schema(raw)
    layers.append(LayerResult(name="schema", ok=schema_ok, issues=schema_issues))
    if not schema_ok:
        report = VerifierReport(run_dir=run_dir, scorecard_path=scorecard_path,
                                layers=layers, accepted=False)
        report.write(run_dir / "verifier_report.json")
        return report

    scorecard = Scorecard.from_dict(raw)

    plaus_ok, plaus_issues = check_plausibility(scorecard)
    layers.append(LayerResult(name="plausibility", ok=plaus_ok, issues=plaus_issues))

    trace_ok, trace_issues, trace_detail = check_trace(scorecard, run_dir)
    layers.append(LayerResult(name="trace", ok=trace_ok, issues=trace_issues, details=trace_detail))

    accepted = all(l.ok for l in layers)
    report = VerifierReport(run_dir=run_dir, scorecard_path=scorecard_path,
                            layers=layers, accepted=accepted)
    report.write(run_dir / "verifier_report.json")
    return report
