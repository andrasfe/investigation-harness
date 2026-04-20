"""static_analysis tool — SPEC § 3.3.

Lightweight text-based probes for testability signals. Deliberately NOT
running heavyweight tools like PMD / SpotBugs in the first cut: grep-
level density counts answer the SPEC's `reflection_density` /
`static_state_density` / `filesystem_assumptions` questions adequately
and leave heavier analysis as a future tool addition (flagged via
escalation when a repo's signals are ambiguous).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..agent_context import AgentContext
from ..llm import ToolSpec


_JAVA_REFLECTION_PATTERNS = [
    r"\bjava\.lang\.reflect\b",
    r"\bClass\.forName\(",
    r"\.getDeclaredMethod\(",
    r"\.getDeclaredField\(",
    r"\.setAccessible\(",
    r"\bMethod\s+\w+\s*=",
    r"\.newInstance\(",
    r"@SuppressWarnings\(\"unchecked\"\)",
]

_STATIC_STATE_PATTERNS = [
    r"\bpublic\s+static\s+(?!final\b)\w",   # non-final public static
    r"\bprotected\s+static\s+(?!final\b)\w",
    r"\bprivate\s+static\s+(?!final\b)\w",
    r"\bSystem\.getProperty\(",
    r"\bSystem\.setProperty\(",
]

_FILESYSTEM_PATTERNS = [
    r"\bnew\s+File\(\"/\b",
    r"\bFiles\.createTempFile\(",
    r"\bFiles\.createTempDirectory\(",
    r"\buser\.home\b",
    r"\busr/local/",
    r"\bC:\\\\",
]

_EXTERNAL_SERVICE_PATTERNS = [
    (r"\bjdbc:\w+:", "jdbc"),
    (r"https?://(?!localhost)", "http_outbound"),
    (r"\bSocket\s*\(", "socket"),
    (r"\bamqp://", "amqp"),
    (r"\bmongodb://", "mongodb"),
    (r"\bredis://", "redis"),
]

_THREAD_SLEEP = re.compile(r"\bThread\.sleep\(")


def _count_matches(files: list[Path], patterns: list[str]) -> int:
    regexes = [re.compile(p) for p in patterns]
    total = 0
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for rx in regexes:
            total += len(rx.findall(text))
    return total


def _collect_java_files(root: Path, module: str | None, limit: int = 5000) -> list[Path]:
    base = root / module if module else root
    files: list[Path] = []
    if not base.exists():
        return []
    # main sources only — tests skew the signal.
    for p in base.rglob("src/main/java/**/*.java"):
        files.append(p)
        if len(files) >= limit:
            break
    if not files:
        # fallback — some modules have non-standard layouts
        for p in base.rglob("*.java"):
            if "/test/" in str(p) or "\\test\\" in str(p):
                continue
            files.append(p)
            if len(files) >= limit:
                break
    return files


def _density_bucket(ratio: float) -> str:
    # occurrences per file
    if ratio >= 2.0:
        return "high"
    if ratio >= 0.5:
        return "medium"
    return "low"


def _make_static_analysis(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
        if ctx.repo_checkout is None:
            return {"ok": False, "error": "repo not cloned yet"}
        metric = str(args.get("metric", "all"))
        module = args.get("module")
        files = _collect_java_files(ctx.repo_checkout, module)
        if not files:
            return {"ok": False, "error": "no Java source files found", "module": module}

        out: dict[str, Any] = {"ok": True, "module": module, "java_files": len(files)}
        nfiles = max(len(files), 1)

        if metric in {"all", "reflection"}:
            refl = _count_matches(files, _JAVA_REFLECTION_PATTERNS)
            out["reflection_hits"] = refl
            out["reflection_density"] = _density_bucket(refl / nfiles)

        if metric in {"all", "static_state"}:
            ss = _count_matches(files, _STATIC_STATE_PATTERNS)
            out["static_state_hits"] = ss
            out["static_state_density"] = _density_bucket(ss / nfiles)

        if metric in {"all", "filesystem"}:
            fs = _count_matches(files, _FILESYSTEM_PATTERNS)
            out["filesystem_hits"] = fs
            out["filesystem_assumptions"] = _density_bucket(fs / nfiles)

        if metric in {"all", "thread_sleep"}:
            count = 0
            for f in files:
                try:
                    count += len(_THREAD_SLEEP.findall(f.read_text(errors="ignore")))
                except OSError:
                    continue
            out["thread_sleep_count"] = count

        if metric in {"all", "external_services"}:
            found: dict[str, int] = {}
            for f in files:
                try:
                    txt = f.read_text(errors="ignore")
                except OSError:
                    continue
                for pattern, label in _EXTERNAL_SERVICE_PATTERNS:
                    if re.search(pattern, txt):
                        found[label] = found.get(label, 0) + 1
            out["external_service_dependencies"] = sorted(found.keys())
            out["external_service_hits"] = found

        # record evidence for every field we populated
        for field, key in [
            ("testability_signals.reflection_density", "reflection_density"),
            ("testability_signals.static_state_density", "static_state_density"),
            ("testability_signals.filesystem_assumptions", "filesystem_assumptions"),
            ("testability_signals.thread_sleep_count", "thread_sleep_count"),
            ("testability_signals.external_service_dependencies", "external_service_dependencies"),
        ]:
            if key in out:
                ctx.record_evidence(field, {"tool": "static_analysis", "value": out[key]})
        return out

    return ToolSpec(
        name="static_analysis",
        description=(
            "Compute testability signals over Java main sources: reflection density, "
            "static-state density, filesystem assumptions, Thread.sleep count, "
            "external-service dependencies. `metric='all'` returns everything in one call."
        ),
        parameters={
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": ["all", "reflection", "static_state", "filesystem", "thread_sleep", "external_services"],
                },
                "module": {"type": "string", "description": "submodule directory to scope the analysis."},
            },
        },
        fn=_fn,
    )


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802
    return [_make_static_analysis(ctx)]
