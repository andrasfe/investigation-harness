"""Layer 2 — plausibility checks.

Numeric ranges, date parsing, inter-field consistency.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from ..models import Scorecard


def _parse_date(s: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


def check_plausibility(s: Scorecard) -> tuple[bool, list[str]]:
    issues: list[str] = []
    today = date.today()

    # test_pass_rate between 0 and 1
    pr = s.tests.test_pass_rate
    if pr < 0.0 or pr > 1.0:
        issues.append(f"tests.test_pass_rate {pr} outside [0,1]")
    # test_run_succeeded=true with test_count=0 is not "success"; it's "no tests ran"
    if s.tests.test_run_succeeded and s.tests.test_count == 0:
        issues.append("tests.test_run_succeeded=true but test_count=0 (no tests actually ran)")
    # build_system="other" contradicts clean_build_succeeded=true
    if s.build.clean_build_succeeded and s.build.build_system == "other":
        issues.append("build.clean_build_succeeded=true but build_system='other' (run_build couldn't identify the system)")

    # coverage percentages
    for field_name, val in (
        ("line_coverage_percent_overall", s.coverage.line_coverage_percent_overall),
        ("branch_coverage_percent_overall", s.coverage.branch_coverage_percent_overall),
    ):
        if val < 0.0 or val > 100.0:
            issues.append(f"coverage.{field_name} {val} outside [0,100]")

    for m in s.coverage.per_module_coverage:
        if not (0.0 <= m.line_coverage <= 100.0):
            issues.append(f"coverage.per_module_coverage[{m.module}].line_coverage {m.line_coverage} outside [0,100]")
        if not (0.0 <= m.branch_coverage <= 100.0):
            issues.append(f"coverage.per_module_coverage[{m.module}].branch_coverage {m.branch_coverage} outside [0,100]")
        if m.loc < 0:
            issues.append(f"coverage.per_module_coverage[{m.module}].loc negative")

    # commit counts non-negative
    if s.bug_history.closed_bug_issues_24mo < 0:
        issues.append("bug_history.closed_bug_issues_24mo negative")
    if s.bug_history.bug_fix_commits_24mo < 0:
        issues.append("bug_history.bug_fix_commits_24mo negative")
    if s.maintainer_activity.commits_last_12mo < 0:
        issues.append("maintainer_activity.commits_last_12mo negative")
    if s.maintainer_activity.distinct_committers_12mo < 0:
        issues.append("maintainer_activity.distinct_committers_12mo negative")

    # Dates parseable and not in the future
    if s.repo_metadata.last_commit_date:
        d = _parse_date(s.repo_metadata.last_commit_date)
        if d is None:
            issues.append(f"repo_metadata.last_commit_date unparseable: {s.repo_metadata.last_commit_date!r}")
        elif d > today:
            issues.append(f"repo_metadata.last_commit_date {d} is in the future")

    if s.maintainer_activity.last_release_date:
        d = _parse_date(s.maintainer_activity.last_release_date)
        if d is None:
            issues.append(f"maintainer_activity.last_release_date unparseable")
        elif d > today:
            issues.append("maintainer_activity.last_release_date in the future")

    # Composite score matches weighted sum within tolerance
    from ..models import compute_composite
    expected = compute_composite(s.score)
    delta = abs(expected - s.score.composite)
    if delta > 0.05:
        issues.append(f"score.composite {s.score.composite} disagrees with recomputation {expected} (Δ={delta:.3f})")

    # LOC consistency: per-module LOC sum should be in rough agreement with
    # any overall LOC if specified. SPEC allows tolerance; we only check that
    # per-module total is non-negative and module list non-empty when overall
    # coverage is non-zero.
    if s.coverage.line_coverage_percent_overall > 0 and not s.coverage.per_module_coverage:
        issues.append("coverage.line_coverage_percent_overall>0 but per_module_coverage is empty")

    # Viable-target sanity: if viable, build + test must have succeeded
    if s.recommendation.viable_target:
        if not s.build.clean_build_succeeded:
            issues.append("recommendation.viable_target=true requires clean_build_succeeded=true")
        if not s.tests.test_run_succeeded:
            issues.append("recommendation.viable_target=true requires tests.test_run_succeeded=true")
        # V2: structured viability justification required.
        ve = s.recommendation.viability_evidence or []
        if len(ve) < 3:
            issues.append(
                f"recommendation.viable_target=true requires >=3 viability_evidence items; got {len(ve)}"
            )
        for i, item in enumerate(ve):
            if not item.criterion:
                issues.append(f"viability_evidence[{i}].criterion empty")
            if not item.metric:
                issues.append(f"viability_evidence[{i}].metric empty")
            if item.observed_value is None and item.observed_value != 0:
                issues.append(f"viability_evidence[{i}].observed_value missing (cite a real value)")
        # At least one evidence item must be satisfied for each of the
        # three core viability criteria: build_tractable, coverage_gap, testability_tractable.
        seen = {item.criterion for item in ve if item.satisfied}
        required = {"build_tractable", "coverage_gap", "testability_tractable"}
        missing = required - seen
        if missing:
            issues.append(f"viability_evidence missing satisfied items for: {sorted(missing)}")

    return (len(issues) == 0, issues)
