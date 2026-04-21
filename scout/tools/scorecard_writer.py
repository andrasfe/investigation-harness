"""finalize_scorecard — SPEC § 3.3. Terminates the evaluation.

Implements the pre-finalize review hook: when ESCALATE=1 the student
escalates the DRAFT scorecard + a verifier dry-run to the teacher before
committing. This is the V3 moment in the viability validation pipeline —
the teacher gets to semantically validate the student's judgment before
the run terminates. Teacher verdicts honoured:

    skip      → approve as-is, write the draft verbatim
    patch     → apply Resolution.fix as a field-path→value dict merged
                into the draft (teacher may correct specific fields)
    abort     → student raises StudentAbort; no scorecard written
    restart   → student raises StudentRestart; outer driver relaunches

If the channel is disabled the hook is a no-op and we write the draft
straight away (parity with pre-review behaviour).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from ..agent_context import AgentContext
from ..llm import ToolSpec
from ..models import Scorecard, compute_composite
from ..supervisor_channel import StudentAbort, StudentRestart


def _escalate_env_gate() -> bool:
    return os.environ.get("ESCALATE", "").strip().lower() in {"1", "true", "yes", "on"}

log = logging.getLogger(__name__)


class ScorecardFinalized(BaseException):
    """Signals the agent loop to exit after the student writes its final scorecard."""


def _pre_verify_summary(sc: Scorecard) -> dict[str, Any]:
    """Cheap self-verify the student runs before escalating.

    Mirrors the Layer-2 plausibility criteria that most often fire so the
    teacher sees the most likely rejection reasons without re-reading the
    full verifier source.
    """
    issues: list[str] = []
    if sc.recommendation.viable_target:
        if not sc.build.clean_build_succeeded:
            issues.append("viable_target=true but clean_build_succeeded=false")
        if not sc.tests.test_run_succeeded:
            issues.append("viable_target=true but test_run_succeeded=false")
        ve_count = len(sc.recommendation.viability_evidence or [])
        if ve_count < 3:
            issues.append(f"viable_target=true but viability_evidence has only {ve_count} items")
    if sc.tests.test_pass_rate < 0.0 or sc.tests.test_pass_rate > 1.0:
        issues.append(f"test_pass_rate={sc.tests.test_pass_rate} outside [0,1]")
    if sc.coverage.line_coverage_percent_overall > 0 and not sc.coverage.per_module_coverage:
        issues.append("line_coverage>0 but per_module_coverage is empty")
    return {"likely_rejection_reasons": issues}


def _autofill_from_trace(draft: dict[str, Any], ctx: AgentContext) -> tuple[dict[str, Any], list[str]]:
    """Auto-fill scorecard fields from the tool trace when the student left
    them at defaults.

    The weak student LLM sometimes forgets to echo a tool result into the
    scorecard. Rather than fail the run, we walk the agent_trace and, for
    every observed tool result, set the matching scorecard field IF AND
    ONLY IF the current draft has the default value (0, 0.0, "", False).
    We never overwrite a non-default value the student chose deliberately.

    Filled-in paths are recorded in metadata.autofilled_fields for audit.
    """
    filled: list[str] = []

    def _get(d: dict[str, Any], path: list[str]) -> Any:
        cur = d
        for p in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        return cur

    def _set(d: dict[str, Any], path: list[str], value: Any) -> bool:
        cur = d
        for p in path[:-1]:
            cur = cur.setdefault(p, {})
            if not isinstance(cur, dict):
                return False
        cur[path[-1]] = value
        return True

    def _is_default(v: Any) -> bool:
        if v is None: return True
        if isinstance(v, bool): return v is False
        if isinstance(v, (int, float)): return v == 0
        if isinstance(v, str): return v == ""
        if isinstance(v, list): return len(v) == 0
        return False

    # Walk the raw tool trace and extract data the student may have dropped.
    github_repo_data: dict[str, Any] = {}
    github_releases: dict[str, Any] = {}
    contributors_len = None
    commits_len = None
    for entry in ctx.tool_trace:
        tool = entry.get("tool")
        r = entry.get("result") or {}
        if not r.get("ok"): continue
        if tool == "run_build":
            for fp, tk in (
                ("build.build_system", "build_system"),
                ("build.clean_build_succeeded", "clean_build_succeeded"),
                ("build.clean_build_time_seconds", "clean_build_time_seconds"),
            ):
                if tk in r and _is_default(_get(draft, fp.split("."))):
                    if _set(draft, fp.split("."), r[tk]): filled.append(fp)
        elif tool == "run_tests":
            # Copy count / rate / time first.
            for fp, tk in (
                ("tests.test_count", "test_count"),
                ("tests.test_pass_rate", "test_pass_rate"),
                ("tests.test_run_time_seconds", "test_run_time_seconds"),
            ):
                if tk in r and _is_default(_get(draft, fp.split("."))):
                    if _set(draft, fp.split("."), r[tk]): filled.append(fp)
            # test_run_succeeded is only true when the tool ran AND produced
            # a non-zero test count. Matches the plausibility rule: "succeeded
            # with zero tests is not success". Covers the dry-run case where
            # run_tests stubs ok=true but test_count=0.
            if _is_default(_get(draft, ["tests", "test_run_succeeded"])):
                test_count = int(_get(draft, ["tests", "test_count"]) or 0)
                consistent = bool(r.get("test_run_succeeded")) and test_count > 0
                if _set(draft, ["tests", "test_run_succeeded"], consistent):
                    filled.append("tests.test_run_succeeded")
        elif tool == "run_coverage":
            for fp, tk in (
                ("coverage.tool_used", "tool_used"),
                ("coverage.line_coverage_percent_overall", "line_coverage_percent_overall"),
                ("coverage.branch_coverage_percent_overall", "branch_coverage_percent_overall"),
                ("coverage.per_module_coverage", "per_module_coverage"),
            ):
                if tk in r and _is_default(_get(draft, fp.split("."))):
                    if _set(draft, fp.split("."), r[tk]): filled.append(fp)
        elif tool == "git_log_analyze":
            if _is_default(_get(draft, ["bug_history", "bug_fix_commits_24mo"])) and "bug_fix_commit_count" in r:
                _set(draft, ["bug_history", "bug_fix_commits_24mo"], r["bug_fix_commit_count"])
                filled.append("bug_history.bug_fix_commits_24mo")
            if _is_default(_get(draft, ["bug_history", "sampled_bug_fixes"])) and r.get("sampled"):
                transformed = []
                for s in r["sampled"]:
                    transformed.append({
                        "commit_sha": s.get("sha", ""),
                        "commit_message_excerpt": (s.get("subject") or "")[:200],
                        "files_changed": s.get("files_changed", [])[:10],
                        "plausibly_test_catchable": True,  # heuristic default — student can override
                        "rationale": "auto-extracted from git_log_analyze",
                    })
                _set(draft, ["bug_history", "sampled_bug_fixes"], transformed)
                filled.append("bug_history.sampled_bug_fixes")
        elif tool == "static_analysis":
            for fp, tk in (
                ("testability_signals.reflection_density", "reflection_density"),
                ("testability_signals.static_state_density", "static_state_density"),
                ("testability_signals.filesystem_assumptions", "filesystem_assumptions"),
                ("testability_signals.thread_sleep_count", "thread_sleep_count"),
                ("testability_signals.external_service_dependencies", "external_service_dependencies"),
            ):
                if tk in r and _is_default(_get(draft, fp.split("."))):
                    if _set(draft, fp.split("."), r[tk]): filled.append(fp)
        elif tool == "github_api_query":
            endpoint = (entry.get("args") or {}).get("endpoint", "")
            data = r.get("data")
            if endpoint.endswith("}") and isinstance(data, dict):
                # /repos/{owner}/{repo}
                github_repo_data = data
            elif endpoint.endswith("/releases/latest") and isinstance(data, dict):
                github_releases = data
            elif "/contributors" in endpoint and isinstance(data, list):
                contributors_len = len(data)
            elif "/commits" in endpoint and isinstance(data, list):
                # take the last /commits list we see
                commits_len = len(data)

    # Apply github-derived metadata.
    if github_repo_data:
        if _is_default(_get(draft, ["repo_metadata", "name"])):
            _set(draft, ["repo_metadata", "name"], github_repo_data.get("name", ""))
            filled.append("repo_metadata.name")
        if _is_default(_get(draft, ["repo_metadata", "stars"])):
            _set(draft, ["repo_metadata", "stars"], int(github_repo_data.get("stargazers_count", 0) or 0))
            filled.append("repo_metadata.stars")
        if _is_default(_get(draft, ["repo_metadata", "primary_license"])):
            lic = (github_repo_data.get("license") or {}).get("spdx_id") or ""
            _set(draft, ["repo_metadata", "primary_license"], lic)
            filled.append("repo_metadata.primary_license")
        if _is_default(_get(draft, ["repo_metadata", "last_commit_date"])):
            _set(draft, ["repo_metadata", "last_commit_date"], github_repo_data.get("pushed_at", ""))
            filled.append("repo_metadata.last_commit_date")
    # last_release_date: only populate if we have a real value. If the
    # /releases/latest endpoint 404'd or published_at is missing, leave as
    # JSON null (NOT the string "null" which the LLM sometimes writes).
    mrel = _get(draft, ["maintainer_activity", "last_release_date"])
    if isinstance(mrel, str) and mrel.strip().lower() in {"null", "none", ""}:
        _set(draft, ["maintainer_activity", "last_release_date"], None)
        filled.append("maintainer_activity.last_release_date:null_normalised")
    if github_releases:
        pa = github_releases.get("published_at")
        if pa and _get(draft, ["maintainer_activity", "last_release_date"]) is None:
            _set(draft, ["maintainer_activity", "last_release_date"], pa)
            filled.append("maintainer_activity.last_release_date")
    if contributors_len is not None and _is_default(_get(draft, ["maintainer_activity", "distinct_committers_12mo"])):
        _set(draft, ["maintainer_activity", "distinct_committers_12mo"], contributors_len)
        filled.append("maintainer_activity.distinct_committers_12mo")
    if commits_len is not None and _is_default(_get(draft, ["maintainer_activity", "commits_last_12mo"])):
        _set(draft, ["maintainer_activity", "commits_last_12mo"], commits_len)
        filled.append("maintainer_activity.commits_last_12mo")

    return draft, filled


def _apply_patch(draft: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a teacher patch into the draft scorecard.

    The patch is a flat field-path → value dict, e.g.
    ``{"build.clean_build_succeeded": true, "score.testability": 7}``.
    Unknown paths are ignored with a warning; non-dict intermediate nodes
    are left untouched (refuse to clobber unknown structure).
    """
    out = json.loads(json.dumps(draft))  # deep copy via serialization
    for path, value in (patch or {}).items():
        if not isinstance(path, str) or "." not in path:
            log.warning("scorecard patch: ignoring flat key %r (use dotted path)", path)
            continue
        parts = path.split(".")
        cursor = out
        for p in parts[:-1]:
            if p not in cursor or not isinstance(cursor[p], dict):
                log.warning("scorecard patch: ignoring %s (path not in draft)", path)
                cursor = None
                break
            cursor = cursor[p]
        if cursor is None:
            continue
        cursor[parts[-1]] = value
    return out


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

        # Auto-fill any scorecard fields the student left at defaults from
        # the tool trace. Safety net for the weak-LLM synthesis gap observed
        # in rounds 0-4. Never overwrites a non-default value the student set.
        # The list of autofilled paths is written to a sidecar file rather
        # than the scorecard's metadata block, because the Scorecard
        # dataclass schema is prohibited from accepting new fields.
        scorecard_dict, autofilled = _autofill_from_trace(scorecard_dict, ctx)

        # Mechanical viability downgrade (round-7 from observed round-6 failures):
        # if tests didn't actually run (test_count=0) OR the build didn't
        # succeed, recommendation.viable_target MUST be false. The weak LLM
        # over-claims viability despite the prompt rule; this is the safety net.
        rec = scorecard_dict.setdefault("recommendation", {})
        if rec.get("viable_target"):
            b = scorecard_dict.get("build", {}) or {}
            t = scorecard_dict.get("tests", {}) or {}
            if not b.get("clean_build_succeeded") or int(t.get("test_count", 0) or 0) == 0:
                rec["viable_target"] = False
                rec["viability_evidence"] = []  # drop stale justification
                n = (rec.get("notes") or "").rstrip()
                suffix = " (auto-downgrade: cannot claim viability when build failed or tests didn't run; dry-run or broken build)"
                if suffix not in n:
                    rec["notes"] = (n + suffix).strip()
                autofilled.append("recommendation.viable_target:auto_downgraded")

        if autofilled:
            log.info("finalize: autofilled %d field(s) from trace: %s",
                     len(autofilled), autofilled)
            sidecar = ctx.config.run_dir / "autofilled_fields.json"
            try:
                sidecar.write_text(
                    json.dumps({"autofilled_fields": sorted(set(autofilled))}, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                log.warning("finalize: failed to write autofill sidecar: %s", exc)

        try:
            draft_scorecard = Scorecard.from_dict(scorecard_dict)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"scorecard did not match schema: {exc}",
                "hint": "Check field names against SPEC.md §3.2.",
            }
        draft_scorecard.score.composite = compute_composite(draft_scorecard.score)

        # -- pre-finalize review escalation (V3) ------------------------------
        # Skips silently when the channel is disabled. Consumes one unit of
        # the escalation budget when it runs. The student MAY legitimately
        # hit budget-exhaustion here and fall through; that just means the
        # draft goes straight to disk and the verifier judges it alone.
        draft_path = ctx.config.run_dir / "scorecard.draft.json"
        draft_scorecard.write(draft_path)
        pre_verify = _pre_verify_summary(draft_scorecard)

        teacher_patch: dict[str, Any] = {}
        if ctx.channel.enabled and _escalate_env_gate() and ctx.escalations_used < ctx.config.escalation_budget:
            log.info("finalize: escalating pre_finalize_review to teacher")
            ctx.escalations_used += 1
            reso = ctx.channel.escalate(
                kind="end_of_cycle_review",
                summary=(
                    f"pre-finalize review for {ctx.config.repo_url}: "
                    f"viable_target={draft_scorecard.recommendation.viable_target}, "
                    f"likely_issues={len(pre_verify['likely_rejection_reasons'])}"
                ),
                context={
                    "phase": "pre_finalize_review",
                    "draft_scorecard": draft_scorecard.to_dict(),
                    "pre_verify": pre_verify,
                    "tool_calls_made": len(ctx.tool_trace),
                    "evidence_keys": sorted(ctx.evidence.keys()),
                },
                artifacts=[
                    str(draft_path),
                    str(ctx.trace_path()),
                    str(ctx.config.run_dir / "evidence.json"),
                ],
                student_hints=[
                    "Read scorecard.draft.json first; then consult evidence.json and agent_trace.jsonl.",
                    "Reply patch={<dotted.path>: <value>, ...} to correct specific fields;",
                    "  reply verdict=skip to approve as-is; abort if the scorecard is unsalvageable.",
                ],
                timeout_sec=float(args.get("review_timeout_sec", 300.0)),
            )
            if reso is None:
                log.warning("finalize: pre-review timed out — writing draft as-is")
            elif reso.verdict == "abort":
                raise StudentAbort(reso.notes or "teacher rejected at pre_finalize_review")
            elif reso.verdict == "restart":
                raise StudentRestart(reso.notes or "teacher requested restart at pre_finalize_review")
            elif reso.verdict == "patch":
                teacher_patch = dict(reso.fix or {})
                log.info("finalize: applying teacher patch to %d field(s)", len(teacher_patch))
            # skip / retry_with / unknown → treat as approve

        # -- apply teacher patch, rebuild scorecard --------------------------
        if teacher_patch:
            patched_dict = _apply_patch(draft_scorecard.to_dict(), teacher_patch)
            md = dict(patched_dict.get("metadata") or {})
            md["teacher_patched_fields"] = sorted(teacher_patch.keys())
            patched_dict["metadata"] = md
            final_scorecard = Scorecard.from_dict(patched_dict)
            final_scorecard.score.composite = compute_composite(final_scorecard.score)
        else:
            final_scorecard = draft_scorecard

        out_path = ctx.config.run_dir / "scorecard.json"
        final_scorecard.write(out_path)

        # Evidence map for the verifier. The tool trace is streamed to
        # agent_trace.jsonl by agent._execute_tool on every call — no bulk
        # flush here (would double-count every entry).
        (ctx.config.run_dir / "evidence.json").write_text(
            json.dumps(ctx.evidence, indent=2, default=str), encoding="utf-8"
        )
        ctx.scorecard_finalized = True
        raise ScorecardFinalized(str(out_path))

    return ToolSpec(
        name="finalize_scorecard",
        description=(
            "Write the final scorecard JSON and end the evaluation. "
            "Pass the complete scorecard as the `scorecard` argument following "
            "SPEC.md §3.2 schema. When the teacher channel is active the "
            "student escalates a pre-finalize review — the teacher may patch "
            "specific fields, approve (skip), or abort. Composite score is "
            "recomputed server-side; send 0.0 for it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "scorecard": {
                    "type": "object",
                    "description": "Full scorecard object, SPEC §3.2 shape.",
                },
                "review_timeout_sec": {
                    "type": "number",
                    "description": "How long to wait for the teacher's pre-finalize reply. Default 300.",
                },
            },
            "required": ["scorecard"],
        },
        fn=_fn,
    )


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802
    return [_make_finalize(ctx)]
