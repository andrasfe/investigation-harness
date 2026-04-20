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


def build_toolset(ctx: AgentContext) -> list[ToolSpec]:
    """Instantiate every tool against the current agent context."""
    tools: list[ToolSpec] = []
    for factory in ALL_FACTORIES:
        tools.extend(factory(ctx))
    # Enforce uniqueness — catches accidental name clashes between modules.
    names = [t.name for t in tools]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise RuntimeError(f"duplicate tool names: {sorted(dupes)}")
    return tools
