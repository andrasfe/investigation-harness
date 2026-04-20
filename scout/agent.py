"""Core agent loop — LLM-driven tool-calling against the fixed scout tool surface.

One instance per role. In single-agent mode exactly one agent (role='full')
runs; in swarm mode (SPEC-adjacent extension) specialists run in parallel
with a judge stage merging their drafts.

The loop itself is deterministic scaffolding (agentic-student-architecture
skill, pattern 2 "deterministic pre-fixes"): we do not ask the LLM to manage
flow control. Every LLM turn either emits tool_calls (we execute them) or
plain text (treated as a stop unless forced to continue).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_context import AgentContext
from .llm import (
    AssistantMessage,
    LLMClient,
    LLMError,
    ToolResult,
    ToolSpec,
    format_assistant_message,
    format_tool_result_message,
)
from .prompts import build_system_prompt
from .supervisor_channel import StudentAbort, StudentRestart
from .tools import build_toolset
from .tools.challenge_tools import ChallengeFinalized
from .tools.scorecard_writer import ScorecardFinalized


def _facts_injection(ctx) -> str:
    """Inject teacher-curated facts into the system prompt.

    Pulls `scope='global'` facts and repo-scoped facts for the current
    evaluation. The channel's facts_store is mtime-gated (reload_if_changed)
    so this is cheap on the hot path but picks up a fact the teacher saves
    mid-run for the next turn.
    """
    if ctx.channel is None or ctx.channel.facts_store is None:
        return ""
    try:
        ctx.channel.facts_store.reload_if_changed()
    except Exception:  # noqa: BLE001
        pass
    # facts_store.match returns globals + the scoped matches, so a single
    # call covers both. Calling twice double-counts global facts.
    all_facts = list(ctx.channel.facts_store.match(
        scope="repo", target=ctx.config.repo_url,
    ))
    if not all_facts:
        return ""
    lines = ["", "# Teacher-curated facts (authoritative; take these as ground truth):"]
    for f in all_facts[:20]:  # cap to keep the system prompt bounded
        lines.append(f"- [{f.scope}:{f.target or '*'}] {f.content}")
    return "\n".join(lines) + "\n"

log = logging.getLogger(__name__)


@dataclass
class AgentRun:
    role: str
    finalized: bool = False
    scorecard_path: Path | None = None
    tool_calls_made: int = 0
    turns: int = 0
    halt_reason: str = ""
    raw_messages: list[dict[str, Any]] = field(default_factory=list)


def _execute_tool(
    tool: ToolSpec, args: dict[str, Any], ctx: AgentContext
) -> dict[str, Any]:
    start = time.time()
    try:
        result = tool.fn(args)
    except ScorecardFinalized:
        raise
    except ChallengeFinalized:
        raise
    except StudentAbort:
        raise
    except StudentRestart:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("tool %s raised", tool.name)
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        ctx.errors.append(f"{tool.name}: {exc}")
    duration = round(time.time() - start, 3)
    entry = {
        "ts": time.time(),
        "tool": tool.name,
        "args": args,
        "result": result,
        "duration_sec": duration,
    }
    ctx.record_trace(entry)
    # Stream-write the trace so a crashed run still has history on disk.
    try:
        with open(ctx.trace_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError as exc:
        log.warning("failed to stream trace: %s", exc)
    return result


def run_agent(
    *,
    ctx: AgentContext,
    client: LLMClient,
    role: str = "full",
    initial_user_message: str | None = None,
    max_turns: int | None = None,
) -> AgentRun:
    """Run one agent to completion (either `finalize_scorecard` or max-turns halt)."""

    system_prompt = build_system_prompt(ctx.config, role=role) + _facts_injection(ctx)
    if initial_user_message is None:
        initial_user_message = (
            f"Begin the evaluation of {ctx.config.repo_url}. "
            f"Follow the phases in the system prompt. Call tools to gather evidence. "
            f"When every field has a value, call finalize_scorecard."
        )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_user_message},
    ]
    # `full` / specialist roles get the full Proposer surface;
    # `challenger` gets the restricted re-verification set.
    tool_role = "challenger" if role == "challenger" else "proposer"
    tools = build_toolset(ctx, role=tool_role)
    tool_map = {t.name: t for t in tools}

    run = AgentRun(role=role)
    max_turns = max_turns or ctx.config.llm.max_tool_calls + 5
    turn_cap = max_turns

    try:
        while run.turns < turn_cap:
            run.turns += 1
            log.info("agent[%s]: turn %d (tool_calls_so_far=%d)", role, run.turns, run.tool_calls_made)
            try:
                assistant = client.chat(messages, tools=tools)
            except LLMError as exc:
                log.error("agent[%s]: llm error: %s", role, exc)
                run.halt_reason = f"llm_error: {exc}"
                ctx.errors.append(str(exc))
                break

            messages.append(format_assistant_message(assistant))
            run.raw_messages.append(assistant.raw)

            if not assistant.tool_calls:
                # Plain-text final response — treat as halt. The scorecard
                # should have been written via finalize_scorecard; if not,
                # the verifier will reject this run.
                run.halt_reason = "no_tool_calls"
                log.info("agent[%s]: halted (no tool calls). text=%s", role, assistant.content[:200])
                break

            # Execute every tool the assistant requested this turn.
            if run.tool_calls_made + len(assistant.tool_calls) > ctx.config.llm.max_tool_calls:
                run.halt_reason = "tool_call_budget_exceeded"
                log.warning("agent[%s]: tool-call budget exceeded", role)
                break

            for tc in assistant.tool_calls:
                tool = tool_map.get(tc.name)
                if tool is None:
                    messages.append(format_tool_result_message(
                        ToolResult(tool_call_id=tc.id, name=tc.name,
                                   content=json.dumps({"ok": False, "error": f"unknown tool {tc.name!r}"})))
                    )
                    continue
                args = tc.arguments
                if "__raw_arguments__" in args:
                    messages.append(format_tool_result_message(
                        ToolResult(tool_call_id=tc.id, name=tc.name,
                                   content=json.dumps({"ok": False, "error": "malformed JSON arguments — fix and retry"})))
                    )
                    continue
                try:
                    result = _execute_tool(tool, args, ctx)
                except ScorecardFinalized as done:
                    run.finalized = True
                    run.scorecard_path = Path(str(done))
                    run.halt_reason = "finalized"
                    log.info("agent[%s]: scorecard written to %s", role, run.scorecard_path)
                    return run
                except ChallengeFinalized as done:
                    run.finalized = True
                    run.scorecard_path = Path(str(done))  # actually challenge.json path
                    run.halt_reason = "challenge_finalized"
                    log.info("agent[%s]: challenge written to %s", role, run.scorecard_path)
                    return run
                run.tool_calls_made += 1
                payload = json.dumps(result, default=str)
                # Cap tool responses fed back to the LLM so context doesn't explode.
                if len(payload) > 12000:
                    payload = payload[:12000] + "… [truncated]"
                messages.append(format_tool_result_message(
                    ToolResult(tool_call_id=tc.id, name=tc.name, content=payload)
                ))
        else:
            run.halt_reason = run.halt_reason or "turn_cap"
    finally:
        # Persist transcript even on error.
        transcript_path = ctx.config.run_dir / f"agent_transcript.{role}.json"
        try:
            transcript_path.write_text(json.dumps(messages, indent=2, default=str), encoding="utf-8")
        except OSError as exc:
            log.warning("failed to write transcript: %s", exc)

    return run
