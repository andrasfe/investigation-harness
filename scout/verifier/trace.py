"""Layer 3 — trace verification.

SPEC § 5.1 Layer 3: every non-zero/non-default scorecard field must be
traceable to a tool call in `agent_trace.jsonl`. This is the primary
defense against the LLM fabricating fields when a probe failed.

Implementation strategy:
  - Load the tool trace (streamed per call by agent.py) and the
    evidence map (recorded by each tool when it populates a field).
  - For every observable scorecard field, confirm at least one matching
    evidence entry exists. Fields the student legitimately couldn't
    populate (left at default/null) are exempt from the check.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..models import Scorecard

log = logging.getLogger(__name__)


# Map of scorecard-field -> tool-call record we require.
# Keys match the paths recorded by AgentContext.record_evidence.
REQUIRED_WHEN_POPULATED: dict[str, str] = {
    # build
    "build.clean_build_succeeded": "run_build",
    "build.clean_build_time_seconds": "run_build",
    "build.build_system": "run_build",
    # tests
    "tests.test_run_succeeded": "run_tests",
    "tests.test_count": "run_tests",
    "tests.test_pass_rate": "run_tests",
    "tests.test_run_time_seconds": "run_tests",
    # coverage
    "coverage.tool_used": "run_coverage",
    "coverage.line_coverage_percent_overall": "run_coverage",
    "coverage.branch_coverage_percent_overall": "run_coverage",
    "coverage.per_module_coverage": "run_coverage",
    # bug history
    "bug_history.bug_fix_commits_24mo": "git_log_analyze",
    "bug_history.sampled_bug_fixes": "git_log_analyze",
    # testability
    "testability_signals.reflection_density": "static_analysis",
    "testability_signals.static_state_density": "static_analysis",
    "testability_signals.filesystem_assumptions": "static_analysis",
    "testability_signals.thread_sleep_count": "static_analysis",
    "testability_signals.external_service_dependencies": "static_analysis",
}


def _load_trace(run_dir: Path) -> list[dict[str, Any]]:
    """Load trace from the per-run JSONL AND from any specialist subdirs."""
    entries: list[dict[str, Any]] = []
    candidates = [run_dir / "agent_trace.jsonl"]
    for sub in run_dir.glob("specialist_*"):
        candidates.append(sub / "agent_trace.jsonl")
    for p in candidates:
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return entries


def _load_evidence(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Merge evidence map from main run + specialist subdirs."""
    merged: dict[str, dict[str, Any]] = {}
    for p in [run_dir / "evidence.json"] + list(run_dir.glob("specialist_*/evidence.json")):
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for k, v in data.items():
            # Specialist-scoped keys look like "build:build.clean_build_succeeded";
            # strip the role prefix so the required map matches.
            clean = k.split(":", 1)[1] if ":" in k and not k.startswith("tests.") else k
            merged[clean] = v
    return merged


def _value_populated(sc: Scorecard, path: str) -> bool:
    """Did the student actually fill this field (vs leave at default/zero)?"""
    parts = path.split(".")
    cur: Any = sc
    for p in parts:
        cur = getattr(cur, p, None)
        if cur is None:
            return False
    if isinstance(cur, bool):
        return True  # both True and False count as populated
    if isinstance(cur, (int, float)):
        return cur != 0
    if isinstance(cur, str):
        return bool(cur)
    if isinstance(cur, list):
        return len(cur) > 0
    return cur is not None


def check_trace(
    sc: Scorecard, run_dir: Path
) -> tuple[bool, list[str], dict[str, Any]]:
    issues: list[str] = []
    trace = _load_trace(run_dir)
    tool_names = {e.get("tool") for e in trace}
    evidence = _load_evidence(run_dir)

    for field_path, expected_tool in REQUIRED_WHEN_POPULATED.items():
        if not _value_populated(sc, field_path):
            continue  # student left this at default — no claim to verify
        if field_path in evidence:
            # Evidence map is the stronger claim — means a tool set this.
            continue
        if expected_tool in tool_names:
            # Fall back to tool-name presence in trace.
            continue
        issues.append(
            f"{field_path} populated but no {expected_tool} tool call in trace"
        )

    # Flag LLM fabrication red-flags: scorecard claims test_run_succeeded=true
    # yet run_tests never ran, etc.
    if sc.build.clean_build_succeeded and "run_build" not in tool_names:
        issues.append("build.clean_build_succeeded=true but run_build never invoked")
    if sc.tests.test_run_succeeded and "run_tests" not in tool_names:
        issues.append("tests.test_run_succeeded=true but run_tests never invoked")

    details = {
        "tool_calls_in_trace": len(trace),
        "distinct_tools": sorted(t for t in tool_names if t),
        "evidence_keys": sorted(evidence.keys()),
    }
    return (len(issues) == 0, issues, details)
