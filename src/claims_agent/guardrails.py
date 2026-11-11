"""Agent-level permissions, enforced in code by Agent SDK hooks.

The MCP server enforces business rules but cannot know which agent is
calling. These hooks can: inside a subagent, the hook input carries
`agent_id` and `agent_type`. Each agent gets an allowlist, and anything
outside it is denied, whatever the model asked for.

The point of the split: the agent that reads untrusted documents
(document-analyst) holds no write tool, and the agent that writes the
decision (the orchestrator) never reads a document directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CORE = "mcp__core__"
SUBAGENT_TOOLS = ("Agent", "Task")  # "Task" is the older name of the same tool.

ALLOWED: dict[str, set[str]] = {
    "orchestrator": {
        *SUBAGENT_TOOLS,
        f"{CORE}get_claim",
        f"{CORE}check_eligibility",
        f"{CORE}record_decision",
    },
    "document-analyst": {f"{CORE}list_claim_documents", f"{CORE}read_document"},
    "coverage-analyst": {f"{CORE}get_policy", f"{CORE}compute_payout"},
}


def agent_of(hook_input: dict[str, Any]) -> str:
    """Which agent is calling. The main thread has no agent_id."""
    if not hook_input.get("agent_id"):
        return "orchestrator"
    return hook_input.get("agent_type") or "unknown-subagent"


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


class Guardrails:
    """PreToolUse: allowlist per agent. PostToolUse: trace of every call, with the agent."""

    def __init__(self, trace_path: Path | None = None):
        self.trace_path = trace_path
        self.denials: list[dict[str, str]] = []

    def _trace(self, entry: dict[str, Any]) -> None:
        if self.trace_path:
            with self.trace_path.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    async def enforce_allowlist(self, hook_input: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        agent = agent_of(hook_input)
        tool = hook_input["tool_name"]
        if tool in ALLOWED.get(agent, set()):
            return {}
        reason = f"{agent} is not allowed to call {tool}."
        self.denials.append({"agent": agent, "tool": tool})
        self._trace({"event": "denied", "agent": agent, "tool": tool, "input": hook_input.get("tool_input")})
        return _deny(reason)

    async def record_call(self, hook_input: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        response = hook_input.get("tool_response")
        text = response if isinstance(response, str) else json.dumps(response, default=str)
        self._trace({
            "event": "tool_call",
            "agent": agent_of(hook_input),
            "tool": hook_input["tool_name"],
            "input": hook_input.get("tool_input"),
            "response": text[:2000],
            "duration_ms": hook_input.get("duration_ms"),
        })
        return {}
