"""git_clone + git_log_analyze tools — SPEC § 3.3."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from ..agent_context import AgentContext
from ..llm import ToolSpec

log = logging.getLogger(__name__)


DEFAULT_BUG_PATTERNS = [
    r"(?i)\bfix(es|ed)?\b",
    r"(?i)\bbug\b",
    r"(?i)\bissue[- ]?\d+\b",
    r"^\[[A-Z]+-\d+\]",        # Apache / Jira style
    r"^#\d+",                  # GitHub issue reference
    r"(?i)\bnpe\b",
    r"(?i)\bclose[sd]? #\d+",
]


def _run_git(
    args: list[str], *, cwd: Path, timeout: int = 180
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _make_git_clone(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url") or ctx.config.repo_url)
        shallow = bool(args.get("shallow", True))
        workspace = ctx.config.workspace_dir
        target = workspace / ctx.config.canonical_repo_name()
        if target.exists() and any(target.iterdir()):
            ctx.repo_checkout = target
            return {
                "ok": True, "cached": True,
                "checkout_path": str(target),
                "note": "workspace already populated; skipping re-clone",
            }
        target.mkdir(parents=True, exist_ok=True)
        cmd = ["clone", "--single-branch"]
        if shallow:
            cmd += ["--depth", "1"]
        cmd += [url, str(target)]
        result = _run_git(cmd, cwd=workspace, timeout=600)
        if result.returncode != 0:
            ctx.errors.append(f"git_clone: {result.stderr[:200]}")
            return {"ok": False, "error": result.stderr[-500:]}
        ctx.repo_checkout = target
        return {
            "ok": True, "cached": False,
            "checkout_path": str(target),
            "stderr_tail": result.stderr[-200:],
        }

    return ToolSpec(
        name="git_clone",
        description=(
            "Clone the target repository into the evaluation workspace. "
            "Safe to call once; subsequent calls return the cached checkout. "
            "Defaults to a shallow (--depth 1) single-branch clone."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Repo URL. Omit to use the evaluation's configured repo_url.",
                },
                "shallow": {
                    "type": "boolean",
                    "description": "Use --depth 1. Disable only if you need full history for git_log_analyze.",
                },
            },
        },
        fn=_fn,
    )


def _make_git_log_analyze(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
        if ctx.repo_checkout is None:
            return {"ok": False, "error": "repo not cloned yet — call git_clone first"}
        since = str(args.get("since", "24.months"))
        patterns_in = args.get("patterns") or DEFAULT_BUG_PATTERNS
        max_sample = int(args.get("max_sample", 10))

        # Unshallow if we cloned shallow — commit stats need history.
        shallow_flag = ctx.repo_checkout / ".git" / "shallow"
        if shallow_flag.exists():
            _run_git(["fetch", "--unshallow"], cwd=ctx.repo_checkout, timeout=600)

        result = _run_git(
            ["log", f"--since={since}", "--format=%H%x09%s%x09%an", "--no-merges"],
            cwd=ctx.repo_checkout,
            timeout=300,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr[-400:]}

        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        regexes = [re.compile(p) for p in patterns_in]
        matches: list[dict[str, Any]] = []
        for ln in lines:
            parts = ln.split("\t", 2)
            if len(parts) < 2:
                continue
            sha, subj, *_ = parts
            if any(rx.search(subj) for rx in regexes):
                matches.append({"sha": sha, "subject": subj.strip()})

        samples = matches[:max_sample]
        for m in samples:
            files_res = _run_git(
                ["show", "--stat", "--format=", m["sha"]],
                cwd=ctx.repo_checkout, timeout=60,
            )
            if files_res.returncode == 0:
                files: list[str] = []
                for ln in files_res.stdout.splitlines():
                    ln = ln.strip()
                    if "|" in ln:
                        fname = ln.split("|", 1)[0].strip()
                        if fname:
                            files.append(fname)
                m["files_changed"] = files[:25]

        out = {
            "ok": True,
            "total_commits_scanned": len(lines),
            "bug_fix_commit_count": len(matches),
            "patterns_used": patterns_in,
            "sampled": samples,
        }
        ctx.record_evidence("bug_history.bug_fix_commits_24mo", {"tool": "git_log_analyze", "value": len(matches)})
        ctx.record_evidence("bug_history.sampled_bug_fixes", {"tool": "git_log_analyze", "sample_size": len(samples)})
        return out

    return ToolSpec(
        name="git_log_analyze",
        description=(
            "Analyze commit history for bug-fix commits matching regex patterns. "
            "Returns count + sampled commits (with files_changed per sample). "
            "If the working copy is shallow, the tool will unshallow first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "git --since value, default '24.months'"},
                "patterns": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Regex patterns. Omit to use scout's defaults (covers Apache [PROJ-###] style).",
                },
                "max_sample": {"type": "integer", "description": "How many matched commits to return in full, default 10."},
            },
        },
        fn=_fn,
    )


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802  (factory style)
    return [_make_git_clone(ctx), _make_git_log_analyze(ctx)]
