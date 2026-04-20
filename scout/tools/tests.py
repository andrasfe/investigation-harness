"""run_tests tool — SPEC § 3.3."""

from __future__ import annotations

import logging
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..agent_context import AgentContext
from ..llm import ToolSpec
from .build import _pick_wrapper, detect_build_system

log = logging.getLogger(__name__)


def _parse_surefire(checkout: Path) -> dict[str, int]:
    """Aggregate JUnit XML reports from target/surefire-reports or build/test-results."""
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    candidates = list(checkout.rglob("surefire-reports/TEST-*.xml"))
    candidates += list(checkout.rglob("test-results/test/TEST-*.xml"))
    candidates += list(checkout.rglob("test-results/**/TEST-*.xml"))
    for path in candidates:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for key in totals:
            try:
                totals[key] += int(root.attrib.get(key, 0) or 0)
            except ValueError:
                pass
    return totals


def _make_run_tests(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
        if ctx.repo_checkout is None:
            return {"ok": False, "error": "repo not cloned yet"}
        system = args.get("system") or ctx.build_system_detected or detect_build_system(ctx.repo_checkout)
        module = args.get("module")
        timeout = int(args.get("timeout_sec", 1800))

        if system not in {"maven", "gradle"}:
            return {"ok": False, "error": f"unsupported build system '{system}'", "should_escalate": True}

        argv, used_wrapper = _pick_wrapper(ctx.repo_checkout, system)
        if not argv or not argv[0]:
            return {"ok": False, "error": f"no runner for {system}"}

        if system == "maven":
            cmd = argv + ["-B", "-ntp"]
            if module:
                cmd += ["-pl", str(module), "-am"]
            cmd += ["test"]
        else:
            cmd = argv + ["--no-daemon", "-q"]
            if module:
                cmd += [f":{module}:test"]
            else:
                cmd += ["test"]

        if ctx.config.dry_run:
            return {
                "ok": True, "dry_run": True,
                "test_run_succeeded": True,
                "test_count": 0, "test_pass_rate": 1.0,
                "test_run_time_seconds": 0,
                "cmd": cmd,
            }

        log_path = ctx.config.run_dir / f"tests_{system}.log"
        start = time.time()
        try:
            result = subprocess.run(
                cmd, cwd=str(ctx.repo_checkout),
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            duration = int(time.time() - start)
            ctx.errors.append(f"run_tests timeout after {duration}s")
            return {
                "ok": False, "timed_out": True,
                "test_run_succeeded": False,
                "test_count": 0, "test_pass_rate": 0.0,
                "test_run_time_seconds": duration,
                "should_escalate": True,
            }
        duration = int(time.time() - start)
        log_path.write_text(result.stdout + "\n--- stderr ---\n" + result.stderr, encoding="utf-8")

        totals = _parse_surefire(ctx.repo_checkout)
        total = totals["tests"]
        failing = totals["failures"] + totals["errors"]
        pass_rate = ((total - failing) / total) if total else 0.0

        flaky: list[str] = []
        # Very conservative heuristic: tests that appeared with both pass & fail — we don't re-run yet,
        # so flaky detection is left to a future tool. Keep the field honest.

        out = {
            "ok": result.returncode == 0 or total > 0,
            "test_run_succeeded": result.returncode == 0,
            "test_count": total,
            "totals": totals,
            "test_pass_rate": round(pass_rate, 4),
            "flaky_tests_observed": flaky,
            "test_run_time_seconds": duration,
            "log_path": str(log_path),
            "cmd": cmd,
        }
        ctx.record_evidence("tests.test_run_succeeded", {"tool": "run_tests", "value": out["test_run_succeeded"]})
        ctx.record_evidence("tests.test_count", {"tool": "run_tests", "value": total})
        ctx.record_evidence("tests.test_pass_rate", {"tool": "run_tests", "value": out["test_pass_rate"]})
        ctx.record_evidence("tests.test_run_time_seconds", {"tool": "run_tests", "value": duration})
        return out

    return ToolSpec(
        name="run_tests",
        description=(
            "Run the project's test suite (mvn test / gradle test). Parses JUnit XML "
            "to compute test_count, test_pass_rate, timing. Pass `module` to scope "
            "the run to a single submodule."
        ),
        parameters={
            "type": "object",
            "properties": {
                "system": {"type": "string", "enum": ["maven", "gradle"]},
                "module": {"type": "string", "description": "submodule name, e.g. 'imaging-formats-tiff'"},
                "timeout_sec": {"type": "integer"},
            },
        },
        fn=_fn,
    )


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802
    return [_make_run_tests(ctx)]
