"""Shared per-evaluation state — the object threaded through every tool call.

Keeping this in its own module avoids the circular import between
`scout.tools.__init__` and `scout.agent`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ScoutConfig
from .supervisor_channel import SupervisorChannel

log = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """State accessible to every tool invocation within one evaluation."""

    config: ScoutConfig
    channel: SupervisorChannel
    repo_checkout: Path | None = None
    build_system_detected: str | None = None
    escalations_used: int = 0
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    scorecard_finalized: bool = False
    errors: list[str] = field(default_factory=list)
    # Populated by tools so the scorecard writer + verifier Layer 3 can
    # confirm every scorecard field is backed by a real tool invocation.
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record_evidence(self, field_path: str, payload: dict[str, Any]) -> None:
        """Link a scorecard field to the tool call that produced its data."""
        self.evidence[field_path] = payload

    def record_trace(self, entry: dict[str, Any]) -> None:
        self.tool_trace.append(entry)

    def trace_path(self) -> Path:
        return self.config.run_dir / "agent_trace.jsonl"

    def log_path(self) -> Path:
        return self.config.run_dir / "agent.log"
