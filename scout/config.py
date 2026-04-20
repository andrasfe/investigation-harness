"""Environment + path resolution for scout.

Loads .env from the repo root. Student-side activation matches the
teacher-student-loop skill's two-gate convention (SUPERVISOR_DIR + ESCALATE).

LLM configuration is OpenRouter by default; any OpenAI-compatible endpoint
works if you override SCOUT_LLM_BASE_URL.

Scout-specific env (all optional unless noted):
    OPENROUTER_API_KEY      required for agentic mode
    LLM_MODEL               default model name for the student agent
    LLM_PROVIDER            "openrouter" (default) or "openai"
    SCOUT_LLM_BASE_URL      override (default: https://openrouter.ai/api/v1)
    SCOUT_SWARM_SIZE        1 = single agent (default), >1 = specialist swarm
    SCOUT_PARALLEL_REPOS    1 = sequential (default), N = up to N concurrent evals
    SCOUT_STUDENT_VERSION   content-hash override for the scorecard stamp
    SCOUT_TIME_BUDGET_SEC   per-repo evaluation budget (default 1800)
    SCOUT_ESCALATION_BUDGET max escalations per repo (default 3)
    SCOUT_MAX_TOOL_CALLS    hard cap per agent run (default 60)
    SCOUT_DRY_RUN           "1" skips heavy tools (build/test/coverage) for smoke tests
    GITHUB_TOKEN            optional, raises GitHub rate limit
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO_ROOT / "runs"
STATE_DIR = REPO_ROOT / "state"
CANARY_DIR = REPO_ROOT / "canaries"
ENV_FILE = REPO_ROOT / ".env"


def load_dotenv(path: Path = ENV_FILE) -> None:
    """Minimal .env loader — no shell expansion, no quoting quirks.

    Idempotent. Does not override variables already set in the process env.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    model: str
    base_url: str
    max_tool_calls: int = 60

    def is_configured(self) -> bool:
        return bool(self.api_key)


def load_llm_config() -> LLMConfig:
    provider = os.environ.get("LLM_PROVIDER", "openrouter").lower()
    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        default_base = "https://openrouter.ai/api/v1"
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        default_base = "https://api.openai.com/v1"
    return LLMConfig(
        provider=provider,
        api_key=api_key,
        model=os.environ.get("LLM_MODEL", "google/gemini-2.5-flash-lite"),
        base_url=os.environ.get("SCOUT_LLM_BASE_URL", default_base),
        max_tool_calls=int(os.environ.get("SCOUT_MAX_TOOL_CALLS", "60")),
    )


@dataclass(frozen=True)
class ScoutConfig:
    evaluation_id: str
    repo_url: str
    target_modules: list[str] | None
    run_dir: Path
    workspace_dir: Path
    student_version: str
    llm: LLMConfig
    swarm_size: int = 1
    time_budget_sec: int = 1800
    escalation_budget: int = 3
    github_token: str | None = None
    dry_run: bool = False
    project_handlers_path: Path = STATE_DIR / "project_handlers.jsonl"

    def canonical_repo_name(self) -> str:
        parts = self.repo_url.rstrip("/").replace(".git", "").split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}_{parts[-1]}"
        return parts[-1]


def student_version_hash(source_dir: Path | None = None) -> str:
    src = source_dir or Path(__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(src.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        h.update(p.relative_to(src).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def build_config(
    *,
    evaluation_id: str,
    repo_url: str,
    target_modules: list[str] | None = None,
    run_dir_override: Path | None = None,
) -> ScoutConfig:
    if run_dir_override is not None:
        run_dir = run_dir_override
    else:
        # Prefer SUPERVISOR_DIR (teacher-student convention) when present.
        env_dir = os.environ.get("SUPERVISOR_DIR")
        run_dir = Path(env_dir) if env_dir else (RUNS_DIR / evaluation_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = run_dir / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    version = os.environ.get("SCOUT_STUDENT_VERSION") or student_version_hash()
    return ScoutConfig(
        evaluation_id=evaluation_id,
        repo_url=repo_url,
        target_modules=target_modules,
        run_dir=run_dir,
        workspace_dir=workspace_dir,
        student_version=version,
        llm=load_llm_config(),
        swarm_size=int(os.environ.get("SCOUT_SWARM_SIZE", "1")),
        time_budget_sec=int(os.environ.get("SCOUT_TIME_BUDGET_SEC", "1800")),
        escalation_budget=int(os.environ.get("SCOUT_ESCALATION_BUDGET", "3")),
        github_token=os.environ.get("GITHUB_TOKEN"),
        dry_run=os.environ.get("SCOUT_DRY_RUN", "").lower() in {"1", "true", "yes"},
    )
