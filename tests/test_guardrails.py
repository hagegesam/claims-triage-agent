import json

import pytest

from claims_agent.guardrails import Guardrails, agent_of

MAIN = {}
DOCS = {"agent_id": "a1", "agent_type": "document-analyst"}
COVER = {"agent_id": "a2", "agent_type": "coverage-analyst"}


def call(tool, who, **extra):
    return {"tool_name": tool, "tool_input": {}, **who, **extra}


def test_agent_identity_comes_from_hook_input():
    assert agent_of(call("x", MAIN)) == "orchestrator"
    assert agent_of(call("x", DOCS)) == "document-analyst"


@pytest.mark.parametrize("tool, who, allowed", [
    ("mcp__core__record_decision", MAIN, True),
    ("mcp__core__record_decision", DOCS, False),   # the agent reading untrusted text cannot write
    ("mcp__core__record_decision", COVER, False),
    ("mcp__core__read_document", MAIN, False),     # the agent that writes never reads documents
    ("mcp__core__read_document", DOCS, True),
    ("mcp__core__compute_payout", COVER, True),
    ("mcp__core__compute_payout", DOCS, False),
    ("Agent", MAIN, True),
    ("Agent", DOCS, False),                        # no nested delegation
    ("Bash", MAIN, False),
    ("mcp__core__get_policy", {"agent_id": "a3", "agent_type": "general-purpose"}, False),
])
async def test_allowlist(tool, who, allowed):
    g = Guardrails()
    out = await g.enforce_allowlist(call(tool, who), None, None)
    assert (out == {}) == allowed
    if not allowed:
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert g.denials


async def test_trace_records_agent_and_tool(tmp_path):
    g = Guardrails(trace_path=tmp_path / "t.jsonl")
    await g.record_call(call("mcp__core__read_document", DOCS, tool_response={"text": "hi"}), "id", None)
    entry = json.loads((tmp_path / "t.jsonl").read_text())
    assert entry["agent"] == "document-analyst" and entry["tool"] == "mcp__core__read_document"
