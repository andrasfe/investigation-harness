"""run_coverage tool — SPEC § 3.3.

JaCoCo-first. Parses `jacoco.xml` under standard Maven / Gradle report
paths. For Gradle projects we inject `jacocoTestReport` if a jacoco plugin
is already applied (detected via file search); we do not edit build files.
"""

from __future__ import annotations

import logging
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .. import docker_runner
from ..agent_context import AgentContext
from ..llm import ToolSpec
from .build import _exec, _pick_wrapper, detect_build_system

log = logging.getLogger(__name__)


def _find_jacoco_reports(checkout: Path) -> list[Path]:
    candidates: list[Path] = []
    for name in ("jacoco.xml", "report.xml"):
        candidates += list(checkout.rglob(f"**/jacoco*/{name}"))
        candidates += list(checkout.rglob(f"**/site/jacoco/{name}"))
    seen = set()
    deduped: list[Path] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def _parse_jacoco(path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return {}
    counters = {c.attrib.get("type"): c.attrib for c in root.findall("counter")}
    line = counters.get("LINE", {})
    branch = counters.get("BRANCH", {})

    def _pct(c: dict[str, str]) -> float:
        try:
            covered = int(c.get("covered", 0))
            missed = int(c.get("missed", 0))
            total = covered + missed
            return round(100.0 * covered / total, 2) if total else 0.0
        except ValueError:
            return 0.0

    per_module: list[dict[str, Any]] = []
    for pkg in root.findall("package"):
        name = pkg.attrib.get("name", "(root)")
        p_line = None
        p_branch = None
        loc = 0
        for c in pkg.findall("counter"):
            if c.attrib.get("type") == "LINE":
                p_line = _pct(c.attrib)
                try:
                    loc = int(c.attrib.get("missed", 0)) + int(c.attrib.get("covered", 0))
                except ValueError:
                    loc = 0
            elif c.attrib.get("type") == "BRANCH":
                p_branch = _pct(c.attrib)
        per_module.append({
            "module": name,
            "loc": loc,
            "line_coverage": p_line or 0.0,
            "branch_coverage": p_branch or 0.0,
        })

    return {
        "tool_used": "jacoco",
        "line_coverage_percent_overall": _pct(line),
        "branch_coverage_percent_overall": _pct(branch),
        "per_module_coverage": per_module,
    }


def _has_jacoco_config(checkout: Path) -> bool:
    # crude detection — enough to know whether running a coverage task is even worth trying
    for f in checkout.rglob("pom.xml"):
        try:
            if "jacoco-maven-plugin" in f.read_text(errors="ignore"):
                return True
        except OSError:
            pass
    for f in list(checkout.rglob("build.gradle")) + list(checkout.rglob("build.gradle.kts")):
        try:
            text = f.read_text(errors="ignore")
            if "jacoco" in text.lower():
                return True
        except OSError:
            pass
    return False


def _make_run_coverage(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
        if ctx.repo_checkout is None:
            return {"ok": False, "error": "repo not cloned yet"}
        tool = args.get("tool", "jacoco")
        if tool != "jacoco":
            return {"ok": False, "error": f"coverage tool '{tool}' not yet supported", "should_escalate": True}
        system = args.get("system") or ctx.build_system_detected or detect_build_system(ctx.repo_checkout)
        timeout = int(args.get("timeout_sec", 1800))
        module = args.get("module")

        if system not in {"maven", "gradle"}:
            return {"ok": False, "error": f"unsupported build system '{system}'", "should_escalate": True}
        if not _has_jacoco_config(ctx.repo_checkout):
            return {
                "ok": False,
                "error": "no jacoco plugin configured in build files",
                "tool_used": "jacoco",
                "should_escalate": True,
            }

        argv, _ = _pick_wrapper(ctx.repo_checkout, system)
        if not argv or not argv[0]:
            return {"ok": False, "error": f"no runner for {system}"}

        if system == "maven":
            cmd = argv + ["-B", "-ntp"]
            if module:
                cmd += ["-pl", str(module), "-am"]
            cmd += ["test", "jacoco:report"]
        else:
            cmd = argv + ["--no-daemon", "-q"]
            if module:
                cmd += [f":{module}:test", f":{module}:jacocoTestReport"]
            else:
                cmd += ["test", "jacocoTestReport"]

        if ctx.config.dry_run:
            return {
                "ok": True, "dry_run": True,
                "tool_used": "jacoco",
                "line_coverage_percent_overall": 0.0,
                "branch_coverage_percent_overall": 0.0,
                "per_module_coverage": [],
                "cmd": cmd,
            }

        log_path = ctx.config.run_dir / f"coverage_{system}.log"
        try:
            rc, stdout, stderr, duration, used_docker = _exec(cmd, cwd=ctx.repo_checkout, timeout=timeout)
            log_path.write_text(stdout + "\n--- stderr ---\n" + stderr, encoding="utf-8")
        except subprocess.TimeoutExpired:
            ctx.errors.append(f"run_coverage timeout (host) after {timeout}s")
            return {"ok": False, "timed_out": True, "should_escalate": True}
        except docker_runner.DockerUnavailableError as e:
            ctx.errors.append(f"run_coverage docker preflight failed: {e}")
            return {"ok": False, "error": f"docker requested but unusable: {e}", "should_escalate": True}
        if rc == 124 and used_docker:
            ctx.errors.append(f"run_coverage timeout after {duration}s (docker)")
            return {"ok": False, "timed_out": True, "used_docker": True, "should_escalate": True}

        reports = _find_jacoco_reports(ctx.repo_checkout)
        if not reports:
            return {
                "ok": False,
                "error": "jacoco did not produce a report",
                "log_path": str(log_path),
                "should_escalate": True,
            }
        merged = _parse_jacoco(reports[0])
        out = {
            "ok": True,
            "duration_sec": duration,
            "report_path": str(reports[0]),
            "used_docker": used_docker,
            **merged,
        }
        ctx.record_evidence("coverage.tool_used", {"tool": "run_coverage", "value": "jacoco"})
        ctx.record_evidence(
            "coverage.line_coverage_percent_overall",
            {"tool": "run_coverage", "value": merged.get("line_coverage_percent_overall", 0.0)},
        )
        ctx.record_evidence(
            "coverage.branch_coverage_percent_overall",
            {"tool": "run_coverage", "value": merged.get("branch_coverage_percent_overall", 0.0)},
        )
        ctx.record_evidence(
            "coverage.per_module_coverage",
            {"tool": "run_coverage", "module_count": len(merged.get("per_module_coverage") or [])},
        )
        return out

    return ToolSpec(
        name="run_coverage",
        description=(
            "Run coverage (JaCoCo) and parse the report. Requires the project's build "
            "files to already include the jacoco plugin — scout never edits build configs. "
            "Returns line+branch coverage overall and per-module."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tool": {"type": "string", "enum": ["jacoco"]},
                "system": {"type": "string", "enum": ["maven", "gradle"]},
                "module": {"type": "string"},
                "timeout_sec": {"type": "integer"},
            },
        },
        fn=_fn,
    )


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802
    return [_make_run_coverage(ctx)]
