"""run_build tool — SPEC § 3.3.

Detects the build system then runs `mvn` / `gradle` with a timeout and
captures the log. Installs nothing; wrappers like `./mvnw` / `./gradlew`
are preferred when present.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..agent_context import AgentContext
from ..llm import ToolSpec

log = logging.getLogger(__name__)


def detect_build_system(checkout: Path) -> str:
    if (checkout / "pom.xml").exists():
        return "maven"
    if (checkout / "settings.gradle").exists() or (checkout / "settings.gradle.kts").exists():
        return "gradle"
    if (checkout / "build.gradle").exists() or (checkout / "build.gradle.kts").exists():
        return "gradle"
    if (checkout / "build.sbt").exists():
        return "sbt"
    if (checkout / "BUILD").exists() or (checkout / "WORKSPACE").exists():
        return "bazel"
    return "other"


def _pick_wrapper(checkout: Path, system: str) -> tuple[list[str], bool]:
    """Return (argv, used_wrapper). Wrapper preferred when available."""
    if system == "maven":
        if (checkout / "mvnw").exists():
            return ([str(checkout / "mvnw")], True)
        exe = shutil.which("mvn")
        return ([exe or "mvn"], False)
    if system == "gradle":
        if (checkout / "gradlew").exists():
            return ([str(checkout / "gradlew")], True)
        exe = shutil.which("gradle")
        return ([exe or "gradle"], False)
    return ([], False)


def _make_run_build(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
        if ctx.repo_checkout is None:
            return {"ok": False, "error": "repo not cloned yet — call git_clone first"}
        system = args.get("system") or detect_build_system(ctx.repo_checkout)
        ctx.build_system_detected = system
        extra = list(args.get("args") or [])
        timeout = int(args.get("timeout_sec", 1200))

        if system in {"sbt", "bazel", "other"}:
            return {
                "ok": False,
                "build_system": system,
                "error": f"build system '{system}' not supported by current tool surface — escalate to teacher",
                "should_escalate": True,
            }

        argv, used_wrapper = _pick_wrapper(ctx.repo_checkout, system)
        if not argv or not argv[0]:
            return {"ok": False, "error": f"no runner found for {system}"}

        if system == "maven":
            # Default to a fast clean-compile that skips tests; tests are handled
            # separately by run_tests so the phases stay separable for verifier Layer 3.
            cmd = argv + extra + ["-B", "-ntp", "-DskipTests", "clean", "compile"]
        else:  # gradle
            cmd = argv + extra + ["--no-daemon", "-q", "assemble"]

        if ctx.config.dry_run:
            log.info("dry-run: skipping build: %s", cmd)
            return {
                "ok": True, "dry_run": True,
                "build_system": system,
                "clean_build_time_seconds": 0,
                "clean_build_succeeded": True,
                "log_path": "",
                "cmd": cmd,
            }

        log_path = ctx.config.run_dir / f"build_{system}.log"
        start = time.time()
        try:
            result = subprocess.run(
                cmd, cwd=str(ctx.repo_checkout),
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            log_path.write_text(result.stdout + "\n--- stderr ---\n" + result.stderr, encoding="utf-8")
            duration = int(time.time() - start)
            ok = result.returncode == 0
            issues: list[str] = []
            if not ok:
                # grab the last 10 non-blank stderr lines as issue summary
                tail = [ln for ln in (result.stderr or "").splitlines() if ln.strip()][-10:]
                issues = tail
            out = {
                "ok": ok,
                "build_system": system,
                "used_wrapper": used_wrapper,
                "clean_build_time_seconds": duration,
                "clean_build_succeeded": ok,
                "log_path": str(log_path),
                "issues_tail": issues,
                "cmd": cmd,
            }
            ctx.record_evidence("build.clean_build_succeeded", {"tool": "run_build", "value": ok})
            ctx.record_evidence("build.clean_build_time_seconds", {"tool": "run_build", "value": duration})
            ctx.record_evidence("build.build_system", {"tool": "run_build", "value": system})
            return out
        except subprocess.TimeoutExpired:
            duration = int(time.time() - start)
            log_path.write_text(f"TIMEOUT after {duration}s\n", encoding="utf-8")
            ctx.errors.append(f"run_build timeout after {duration}s")
            return {
                "ok": False, "timed_out": True,
                "build_system": system,
                "clean_build_time_seconds": duration,
                "clean_build_succeeded": False,
                "log_path": str(log_path),
                "should_escalate": True,
            }

    return ToolSpec(
        name="run_build",
        description=(
            "Compile the project in the cloned checkout. Auto-detects maven/gradle "
            "unless `system` is specified. Skips tests (use run_tests for those). "
            "Returns clean_build_time_seconds and a log path. If the build system "
            "is unsupported (sbt/bazel/other), returns should_escalate=true."
        ),
        parameters={
            "type": "object",
            "properties": {
                "system": {
                    "type": "string", "enum": ["maven", "gradle", "sbt", "bazel", "other"],
                    "description": "Override auto-detection.",
                },
                "args": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Extra args spliced before the target goal.",
                },
                "timeout_sec": {"type": "integer", "description": "Hard kill timeout. Default 1200."},
            },
        },
        fn=_fn,
    )


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802
    return [_make_run_build(ctx)]
