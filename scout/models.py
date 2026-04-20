"""Scorecard data model — SPEC.md § 3.2.

The shape is exactly what the verifier's Layer 1 checks against. Keep this
file and the SPEC § 3.2 example in lock-step: any field added here must be
added to the SPEC and to the verifier's schema definition, or Layer 1 will
reject every scorecard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ---------- sub-blocks -------------------------------------------------------


@dataclass
class RepoMetadata:
    name: str = ""
    stars: int = 0
    downloads_last_12mo: int | None = None
    primary_license: str = ""
    last_commit_date: str = ""


@dataclass
class BuildInfo:
    build_system: str = "other"  # maven | gradle | sbt | other
    jdk_version_required: str = ""
    clean_build_time_seconds: int = 0
    clean_build_succeeded: bool = False
    build_log_path: str = ""
    build_issues_encountered: list[str] = field(default_factory=list)


@dataclass
class TestInfo:
    test_run_succeeded: bool = False
    test_count: int = 0
    test_pass_rate: float = 0.0
    flaky_tests_observed: list[str] = field(default_factory=list)
    test_run_time_seconds: int = 0


@dataclass
class ModuleCoverage:
    module: str
    loc: int
    line_coverage: float
    branch_coverage: float


@dataclass
class CoverageInfo:
    tool_used: str = "other"  # jacoco | cobertura | other
    line_coverage_percent_overall: float = 0.0
    branch_coverage_percent_overall: float = 0.0
    per_module_coverage: list[ModuleCoverage] = field(default_factory=list)


@dataclass
class SampledBugFix:
    commit_sha: str
    commit_message_excerpt: str
    files_changed: list[str]
    plausibly_test_catchable: bool
    rationale: str


@dataclass
class BugHistoryInfo:
    closed_bug_issues_24mo: int = 0
    bug_fix_commits_24mo: int = 0
    sampled_bug_fixes: list[SampledBugFix] = field(default_factory=list)


@dataclass
class MaintainerActivityInfo:
    commits_last_12mo: int = 0
    distinct_committers_12mo: int = 0
    median_pr_merge_time_days: float | None = None
    last_release_date: str | None = None


@dataclass
class TestabilitySignals:
    reflection_density: str = "low"  # low | medium | high
    static_state_density: str = "low"
    external_service_dependencies: list[str] = field(default_factory=list)
    thread_sleep_count: int = 0
    filesystem_assumptions: str = "low"


@dataclass
class ScoreBlock:
    build_tractability: int = 0  # 0-10
    coverage_gap_value: int = 0
    testability: int = 0
    bug_history_richness: int = 0
    maintainer_responsiveness: int = 0
    composite: float = 0.0


@dataclass
class Recommendation:
    viable_target: bool = False
    recommended_submodule: str | None = None
    notes: str = ""
    estimated_coverage_delta_achievable: float | None = None


@dataclass
class ScorecardMetadata:
    student_version: str = ""
    evaluation_duration_seconds: int = 0
    escalation_count: int = 0
    errors_encountered: list[str] = field(default_factory=list)


# ---------- top-level scorecard ---------------------------------------------


@dataclass
class Scorecard:
    evaluation_id: str = ""
    repo_url: str = ""
    repo_metadata: RepoMetadata = field(default_factory=RepoMetadata)
    build: BuildInfo = field(default_factory=BuildInfo)
    tests: TestInfo = field(default_factory=TestInfo)
    coverage: CoverageInfo = field(default_factory=CoverageInfo)
    bug_history: BugHistoryInfo = field(default_factory=BugHistoryInfo)
    maintainer_activity: MaintainerActivityInfo = field(
        default_factory=MaintainerActivityInfo
    )
    testability_signals: TestabilitySignals = field(default_factory=TestabilitySignals)
    score: ScoreBlock = field(default_factory=ScoreBlock)
    recommendation: Recommendation = field(default_factory=Recommendation)
    metadata: ScorecardMetadata = field(default_factory=ScorecardMetadata)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scorecard":
        def _wrap(cls_, val):
            if val is None:
                return cls_()
            if isinstance(val, cls_):
                return val
            return cls_(**val)

        rm = _wrap(RepoMetadata, data.get("repo_metadata"))
        bi = _wrap(BuildInfo, data.get("build"))
        ti = _wrap(TestInfo, data.get("tests"))
        cov_raw = data.get("coverage") or {}
        per_module = [
            ModuleCoverage(**m) if not isinstance(m, ModuleCoverage) else m
            for m in cov_raw.get("per_module_coverage", []) or []
        ]
        ci = CoverageInfo(
            tool_used=cov_raw.get("tool_used", "other"),
            line_coverage_percent_overall=float(
                cov_raw.get("line_coverage_percent_overall", 0.0) or 0.0
            ),
            branch_coverage_percent_overall=float(
                cov_raw.get("branch_coverage_percent_overall", 0.0) or 0.0
            ),
            per_module_coverage=per_module,
        )
        bh_raw = data.get("bug_history") or {}
        sampled = [
            SampledBugFix(**s) if not isinstance(s, SampledBugFix) else s
            for s in bh_raw.get("sampled_bug_fixes", []) or []
        ]
        bh = BugHistoryInfo(
            closed_bug_issues_24mo=int(bh_raw.get("closed_bug_issues_24mo", 0) or 0),
            bug_fix_commits_24mo=int(bh_raw.get("bug_fix_commits_24mo", 0) or 0),
            sampled_bug_fixes=sampled,
        )
        ma = _wrap(MaintainerActivityInfo, data.get("maintainer_activity"))
        ts = _wrap(TestabilitySignals, data.get("testability_signals"))
        sb = _wrap(ScoreBlock, data.get("score"))
        rec = _wrap(Recommendation, data.get("recommendation"))
        md = _wrap(ScorecardMetadata, data.get("metadata"))
        return cls(
            evaluation_id=data.get("evaluation_id", ""),
            repo_url=data.get("repo_url", ""),
            repo_metadata=rm,
            build=bi,
            tests=ti,
            coverage=ci,
            bug_history=bh,
            maintainer_activity=ma,
            testability_signals=ts,
            score=sb,
            recommendation=rec,
            metadata=md,
        )


def compute_composite(score: ScoreBlock, weights: dict[str, float] | None = None) -> float:
    """SPEC § 9.2 — weighted combination of the five subscores."""
    w = weights or {
        "build_tractability": 0.25,
        "coverage_gap_value": 0.25,
        "testability": 0.20,
        "bug_history_richness": 0.15,
        "maintainer_responsiveness": 0.15,
    }
    return round(
        w["build_tractability"] * score.build_tractability
        + w["coverage_gap_value"] * score.coverage_gap_value
        + w["testability"] * score.testability
        + w["bug_history_richness"] * score.bug_history_richness
        + w["maintainer_responsiveness"] * score.maintainer_responsiveness,
        3,
    )
