"""Bug-mining is implemented inside git_ops.git_log_analyze.

This module keeps the SPEC § 3.3 tool list explicit (one entry per named
tool) but delegates the actual commit analysis to git_ops. If bug mining
ever grows features that don't belong in the git wrapper (e.g. issue-
tracker cross-reference), add them here as new ToolSpecs.
"""

from __future__ import annotations

from ..agent_context import AgentContext
from ..llm import ToolSpec


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802
    return []
