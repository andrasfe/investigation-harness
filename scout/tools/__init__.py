"""Fixed tool surface for the scout student — SPEC § 3.3.

The student is a constrained agent: it may ONLY invoke the tools registered
here, via the LLM tool-calling interface. Arbitrary shell execution is not
on the tool surface; `run_build` / `run_tests` / `run_coverage` wrap the
build-system binaries behind validated argument schemas.

Every tool returns a plain dict (JSON-serialisable) so the transcript in
`<run_dir>/agent_trace.jsonl` is a complete record of what the student
observed — Layer 3 (trace verification) relies on this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..llm import ToolSpec
from .bug_mining import TOOL_SPECS as BUG_MINING
from .build import TOOL_SPECS as BUILD
from .challenge_tools import TOOL_SPECS as CHALLENGE
from .coverage import TOOL_SPECS as COVERAGE
from .escalate_tool import TOOL_SPECS as ESCALATE
from .git_ops import TOOL_SPECS as GIT
from .github_api import TOOL_SPECS as GH_API
from .scorecard_writer import TOOL_SPECS as SCORECARD
from .static_analysis import TOOL_SPECS as STATIC
from .tests import TOOL_SPECS as TESTS


ToolFactory = Callable[["AgentContext"], list[ToolSpec]]


# Import-order-sensitive: AgentContext is injected; avoid circular import
# by forward-reffing it here.
from ..agent_context import AgentContext  # noqa: E402


ALL_FACTORIES: tuple[ToolFactory, ...] = (
    GIT, BUILD, TESTS, COVERAGE, BUG_MINING, GH_API, STATIC, ESCALATE, SCORECARD,
)

# Challenger role (adversarial evaluation) — read-only re-verifiers plus the
# two challenge-specific tools. NOT git_clone (workspace is already populated),
# NOT run_build/run_tests/run_coverage (Proposer artifacts are frozen), NOT
# finalize_scorecard or escalate (Proposer-only).
CHALLENGER_FACTORIES: tuple[ToolFactory, ...] = (
    GH_API, STATIC, BUG_MINING, GIT, CHALLENGE,
)


def build_toolset(ctx: AgentContext, *, role: str = "proposer") -> list[ToolSpec]:
    """Instantiate the tools available to a given role.

    ``role='proposer'`` → full SPEC §3.3 surface.
    ``role='challenger'`` → the read-only re-verification set plus
    file_challenge + finalize_challenge.
    """
    factories = ALL_FACTORIES if role == "proposer" else CHALLENGER_FACTORIES
    tools: list[ToolSpec] = []
    for factory in factories:
        tools.extend(factory(ctx))
    if role == "challenger":
        # git_clone is idempotent and safe to expose (the workspace is shared);
        # git_log_analyze re-read is genuinely useful for bug-mining challenges.
        # Filter out tools a challenger shouldn't call even if inherited:
        banned = {"run_build", "run_tests", "run_coverage", "escalate", "finalize_scorecard"}
        tools = [t for t in tools if t.name not in banned]
    names = [t.name for t in tools]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise RuntimeError(f"duplicate tool names: {sorted(dupes)}")
    return tools
