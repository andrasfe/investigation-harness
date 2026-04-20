"""OpenRouter / OpenAI-compatible chat-completions client with tool-calling.

Minimal implementation: one class, no SDK dependency. Uses `requests` for
HTTP. Supports the OpenAI tool-calling wire format which OpenRouter
mirrors verbatim (plus a few provider-optional fields it ignores).

Why not use the anthropic or openai SDK? The student targets cheap models
across providers — Gemini, DeepSeek, Qwen, Haiku-over-OpenRouter — and
a single OpenAI-compatible surface is the common denominator. If a
specific model has deviant tool-calling behavior, handle it in
`ToolCallLoop._normalize_assistant_message` rather than forking the client.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when the LLM endpoint returns a non-recoverable error."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantMessage:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    tool_call_id: str
    name: str
    content: str  # JSON-serialized payload


ToolFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class ToolSpec:
    """OpenAI-style tool declaration plus a Python callable."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: ToolFn

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class LLMClient:
    """Thin wrapper around the OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_sec: float = 120.0,
        max_retries: int = 3,
        app_ref: str = "https://github.com/andrasfe/investigation-harness",
        app_name: str = "scout",
    ):
        if not api_key:
            raise LLMError("LLM api_key is empty — check OPENROUTER_API_KEY in .env")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.app_ref = app_ref
        self.app_name = app_name

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSpec] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.1,
        max_tokens: int | None = 2048,
    ) -> AssistantMessage:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.app_ref,
            "X-Title": self.app_name,
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
            else:
                payload["tool_choice"] = "auto"

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=self.timeout_sec
                )
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    raise LLMError(f"LLM transient error {resp.status_code}: {resp.text[:300]}")
                if resp.status_code >= 400:
                    raise LLMError(f"LLM error {resp.status_code}: {resp.text[:500]}")
                return self._parse_response(resp.json())
            except (requests.RequestException, LLMError) as exc:
                last_err = exc
                wait = min(2 ** attempt, 10)
                log.warning("llm: retry %d after %s (%.1fs)", attempt + 1, exc, wait)
                time.sleep(wait)
        assert last_err is not None
        raise LLMError(f"LLM call exhausted retries: {last_err}")

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> AssistantMessage:
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"malformed LLM response: {exc} raw={data!r}") from exc

        content = msg.get("content") or ""
        tcs_raw = msg.get("tool_calls") or []
        tool_calls: list[ToolCall] = []
        for tc in tcs_raw:
            if tc.get("type") and tc["type"] != "function":
                continue
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args_raw = fn.get("arguments", "{}")
            # arguments is a JSON-encoded string per OpenAI schema
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw) if args_raw.strip() else {}
                except json.JSONDecodeError:
                    # Some providers return a malformed blob; wrap it for later repair.
                    args = {"__raw_arguments__": args_raw}
            else:
                args = dict(args_raw)
            tool_calls.append(ToolCall(id=tc.get("id", ""), name=name, arguments=args))
        return AssistantMessage(content=content, tool_calls=tool_calls, raw=msg)


def format_tool_result_message(result: ToolResult) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": result.tool_call_id,
        "name": result.name,
        "content": result.content,
    }


def format_assistant_message(msg: AssistantMessage) -> dict[str, Any]:
    """Round-trip the assistant message back to the provider for next turn.

    We must include the original tool_calls so the provider pairs each
    subsequent `role: tool` response with the right call id.
    """
    out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            for tc in msg.tool_calls
        ]
    return out
