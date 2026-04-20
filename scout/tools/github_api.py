"""github_api_query tool — SPEC § 3.3.

Read-only access to a whitelist of GitHub endpoints. No write operations.
Falls back to unauthenticated requests if no GITHUB_TOKEN is set (lower
rate limit, fewer fields available).
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import requests

from ..agent_context import AgentContext
from ..llm import ToolSpec

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# Endpoint templates we allow the student to call. Anything else is rejected
# at the tool layer — constrains blast radius without trusting the LLM.
ALLOWED_ENDPOINTS = (
    re.compile(r"^/repos/[^/]+/[^/]+$"),                      # repo metadata
    re.compile(r"^/repos/[^/]+/[^/]+/releases(\?.*)?$"),       # releases
    re.compile(r"^/repos/[^/]+/[^/]+/releases/latest$"),
    re.compile(r"^/repos/[^/]+/[^/]+/contributors(\?.*)?$"),   # committers
    re.compile(r"^/repos/[^/]+/[^/]+/pulls(\?.*)?$"),          # PRs for merge-time stats
    re.compile(r"^/repos/[^/]+/[^/]+/issues(\?.*)?$"),         # issue counts
    re.compile(r"^/search/issues(\?.*)?$"),                    # scoped issue search
    re.compile(r"^/repos/[^/]+/[^/]+/commits(\?.*)?$"),
    re.compile(r"^/repos/[^/]+/[^/]+/license$"),
)


def _repo_owner_name(repo_url: str) -> tuple[str, str] | None:
    p = urlparse(repo_url)
    if p.netloc != "github.com":
        return None
    parts = [x for x in p.path.strip("/").split("/") if x]
    if len(parts) < 2:
        return None
    owner, name = parts[0], parts[1].replace(".git", "")
    return owner, name


def _make_github_api_query(ctx: AgentContext):
    def _fn(args: dict[str, Any]) -> dict[str, Any]:
        endpoint = str(args.get("endpoint", "")).strip()
        if not endpoint.startswith("/"):
            return {"ok": False, "error": "endpoint must start with '/'"}
        if not any(rx.match(endpoint) for rx in ALLOWED_ENDPOINTS):
            return {
                "ok": False,
                "error": f"endpoint '{endpoint}' not on scout's allowlist",
                "allowlist": [rx.pattern for rx in ALLOWED_ENDPOINTS],
            }

        # Light template substitution: {owner} / {repo} pulled from config
        info = _repo_owner_name(ctx.config.repo_url)
        if info and "{owner}" in endpoint:
            endpoint = endpoint.replace("{owner}", info[0])
        if info and "{repo}" in endpoint:
            endpoint = endpoint.replace("{repo}", info[1])

        url = GITHUB_API + endpoint
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "scout/0.1"}
        if ctx.config.github_token:
            headers["Authorization"] = f"Bearer {ctx.config.github_token}"

        try:
            resp = requests.get(url, headers=headers, timeout=30, params=args.get("params") or None)
        except requests.RequestException as exc:
            return {"ok": False, "error": f"http error: {exc}"}
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            return {"ok": False, "rate_limited": True, "error": resp.text[:300]}
        if resp.status_code >= 400:
            return {"ok": False, "status": resp.status_code, "error": resp.text[:400]}
        try:
            data = resp.json()
        except ValueError:
            return {"ok": False, "error": "non-JSON response"}

        # Truncate very long list responses so the context doesn't blow up.
        truncated = False
        if isinstance(data, list) and len(data) > 50:
            data = data[:50]
            truncated = True

        return {
            "ok": True, "status": resp.status_code,
            "endpoint": endpoint,
            "data": data,
            "truncated": truncated,
            "rate_limit_remaining": resp.headers.get("x-ratelimit-remaining"),
        }

    return ToolSpec(
        name="github_api_query",
        description=(
            "Read-only GitHub API query. Endpoint must start with '/' and "
            "match an allowlisted pattern (see error message for the list). "
            "Supports {owner}/{repo} substitution from the evaluation's repo_url. "
            "Use this for stars, license, contributors, PR merge times, issue counts."
        ),
        parameters={
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "description": "e.g. '/repos/apache/commons-imaging' or '/repos/{owner}/{repo}/pulls?state=closed'",
                },
                "params": {
                    "type": "object",
                    "description": "Optional query string params merged into the URL.",
                },
            },
            "required": ["endpoint"],
        },
        fn=_fn,
    )


def TOOL_SPECS(ctx: AgentContext) -> list[ToolSpec]:  # noqa: N802
    return [_make_github_api_query(ctx)]
