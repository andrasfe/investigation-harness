"""Layer 1 — schema validation.

Structural check against SPEC § 3.2. Doesn't evaluate values (that's
Layer 2's job); only shape + required fields + enumerated values + types.
"""

from __future__ import annotations

from typing import Any

REQUIRED_TOP_KEYS = {
    "evaluation_id", "repo_url", "repo_metadata", "build", "tests", "coverage",
    "bug_history", "maintainer_activity", "testability_signals",
    "score", "recommendation", "metadata",
}

BUILD_SYSTEMS = {"maven", "gradle", "sbt", "other"}
COV_TOOLS = {"jacoco", "cobertura", "other"}
DENSITY = {"low", "medium", "high"}

SCORE_FIELDS = {
    "build_tractability", "coverage_gap_value", "testability",
    "bug_history_richness", "maintainer_responsiveness", "composite",
}


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_schema(data: dict[str, Any]) -> tuple[bool, list[str]]:
    issues: list[str] = []

    missing = REQUIRED_TOP_KEYS - set(data.keys())
    if missing:
        issues.append(f"missing top-level fields: {sorted(missing)}")

    # repo_metadata
    rm = data.get("repo_metadata") or {}
    for k in ("name", "primary_license", "last_commit_date"):
        if not isinstance(rm.get(k, ""), str):
            issues.append(f"repo_metadata.{k} must be a string")
    if not _is_int(rm.get("stars", 0)):
        issues.append("repo_metadata.stars must be integer")

    # build
    b = data.get("build") or {}
    if b.get("build_system") not in BUILD_SYSTEMS:
        issues.append(f"build.build_system must be one of {sorted(BUILD_SYSTEMS)}")
    if not isinstance(b.get("clean_build_succeeded"), bool):
        issues.append("build.clean_build_succeeded must be boolean")
    if not _is_int(b.get("clean_build_time_seconds", 0)):
        issues.append("build.clean_build_time_seconds must be integer")

    # tests
    t = data.get("tests") or {}
    if not isinstance(t.get("test_run_succeeded"), bool):
        issues.append("tests.test_run_succeeded must be boolean")
    if not _is_int(t.get("test_count", 0)):
        issues.append("tests.test_count must be integer")
    pr = t.get("test_pass_rate", 0.0)
    if not _is_num(pr):
        issues.append("tests.test_pass_rate must be number")

    # coverage
    c = data.get("coverage") or {}
    if c.get("tool_used") not in COV_TOOLS:
        issues.append(f"coverage.tool_used must be one of {sorted(COV_TOOLS)}")
    for k in ("line_coverage_percent_overall", "branch_coverage_percent_overall"):
        if not _is_num(c.get(k, 0.0)):
            issues.append(f"coverage.{k} must be number")
    if not isinstance(c.get("per_module_coverage", []), list):
        issues.append("coverage.per_module_coverage must be list")

    # bug_history
    bh = data.get("bug_history") or {}
    for k in ("closed_bug_issues_24mo", "bug_fix_commits_24mo"):
        if not _is_int(bh.get(k, 0)):
            issues.append(f"bug_history.{k} must be integer")
    if not isinstance(bh.get("sampled_bug_fixes", []), list):
        issues.append("bug_history.sampled_bug_fixes must be list")

    # testability_signals
    ts = data.get("testability_signals") or {}
    for k in ("reflection_density", "static_state_density", "filesystem_assumptions"):
        if ts.get(k, "low") not in DENSITY:
            issues.append(f"testability_signals.{k} must be one of {sorted(DENSITY)}")
    if not _is_int(ts.get("thread_sleep_count", 0)):
        issues.append("testability_signals.thread_sleep_count must be integer")

    # score
    sc = data.get("score") or {}
    for k in SCORE_FIELDS:
        if k == "composite":
            if not _is_num(sc.get(k, 0.0)):
                issues.append("score.composite must be number")
            continue
        v = sc.get(k, 0)
        if not _is_int(v) or not (0 <= v <= 10):
            issues.append(f"score.{k} must be integer 0..10")

    # recommendation
    rec = data.get("recommendation") or {}
    if not isinstance(rec.get("viable_target"), bool):
        issues.append("recommendation.viable_target must be boolean")

    return (len(issues) == 0, issues)
