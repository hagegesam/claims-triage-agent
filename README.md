# Claims triage agent: Claude Agent SDK + MCP

A multi-agent system that triages home-insurance claims against a mock core system, and an eval that measures it against a code-only baseline. Everything is fictional: the insurer (Northwind Mutual), the people, the addresses.

The question it explores: **in a regulated workflow, what should the model decide, and what must code guarantee?**

## Architecture

```
                 ┌──────────────── Agent SDK process ────────────────┐
 "Process        │ orchestrator ── Agent ──► document-analyst         │
  claim X" ────► │   │                        (reads untrusted docs,  │
                 │   │                         no write tool)         │
                 │   └──────── Agent ──► coverage-analyst             │
                 │                        (policy + payout, never     │
                 │                         computes an amount)        │
                 │ hooks: PreToolUse allowlist per agent,             │
                 │        PostToolUse trace with the calling agent    │
                 └──────────────────────┬─────────────────────────────┘
                                        │ MCP (stdio)
                 ┌──────────────────────▼─────────────────────────────┐
                 │ core_server.py → core.py  (deterministic)           │
                 │ eligibility · coverage · payout arithmetic ·        │
                 │ auto-approval limit · required documents ·          │
                 │ one decision per claim · audit log of every call    │
                 └─────────────────────────────────────────────────────┘
```

**Where the model stops and code starts**

| The model decides | Code guarantees |
|---|---|
| The actual cause of loss, from the adjuster's narrative (a "burst pipe" that the adjuster describes as months of seepage is an excluded gradual leak) | Policy in force, premium paid, reported within 30 days |
| Whether documents contradict each other (an invoice dated before the loss) | Payout = min(loss, limit) − deductible, computed by the core system: the model never sends an amount |
| That text inside a document is not an instruction | Approvals above ILS 25,000 go to a human; theft needs a police report |
| Which decision to record, with a rationale a human can check | One decision per claim; every call audited |

**Privilege separation.** The agent that reads untrusted documents (`document-analyst`) has no write tool. The agent that writes the decision (`orchestrator`) never reads a document directly. Two layers enforce this:
- each `AgentDefinition` lists only its own MCP tools;
- a `PreToolUse` hook checks an allowlist per agent, using the `agent_id` / `agent_type` the SDK puts in the hook input. The MCP server cannot know which agent is calling, the hook can.

The business rules live in the MCP server, so they hold for any client, not only this agent.

## Dataset and eval

`dataset.py` generates 36 claims: 12 scenarios × 3 variants, with ground truth.

| Settled by rules (24) | Need reading free text (12) |
|---|---|
| sudden pipe burst, fire, theft with police report, theft capped at limit (approve) · lapsed policy, late report (deny) · above auto-approval limit, theft without police report (refer) | gradual leak described as a burst (deny) · water from outside = flood (deny) · documents contradict each other on dates (refer) · an instruction injected in a claimant letter (decision must not change) |

The eval reads the decision from the **system of record** (`decisions.json`), not from what the agent says it did. Each claim runs on its own copy of the data. Metrics: accuracy (decision and payout), split by rule-settled vs model-needed, **false approvals** (money paid that should not be: the costliest error), confusion matrix, cost and latency per claim, guardrail denials.

The baseline is the same core system with keyword rules on the claimant's description and no model.

## Results

Run of 2026-11-11, 36 claims, 3 in parallel.

| System | Accuracy | Rule-settled | Needs model | False approvals | Cost / claim | Latency p50 |
|---|---|---|---|---|---|---|
| Baseline (code only) | 72% (26/36) | 100% (24/24) | 17% (2/12) | 9 | $0 | 0 s |
| Agent (`claude-opus-5-5`, effort medium) | 100% (36/36) | 100% (24/24) | 100% (12/12) | 0 | $0.11 | 47 s |

Total agent cost: $4.00, as estimated by the SDK.

**What the traces show** (not only the score):
- The 6 ineligible claims stop after the deterministic check, with no subagent: $0.03 and 10 s instead of $0.13 and 47 s.
- On 2 of the 3 claims above the auto-approval limit, the orchestrator first tried to approve. The core system refused and said why, and the orchestrator recorded `refer_to_human`. The refusal-as-data path is used, not just designed.
- Every rationale cites the documents (dates, amounts, the injected letter), so a reviewer can check it in a minute.
- The per-agent allowlist never fired in these runs: the agents stayed within their tools. It is covered by unit tests, not yet by a live attack.

**What this does not show.** 100% on an eval I wrote is a sign the eval is too easy, not that the agent is ready. The documents are templated (3 phrasings per scenario), the contradictions are blunt, and the same person wrote the cases and the prompts. One leak of that kind was found and removed: the document-analyst prompt gave "an invoice dated before the loss" as an example, which is one of the test cases. The 3 contradiction claims were re-run without it and still passed (3/3). So this run is a non-regression guard and a cost/latency baseline, not a quality claim.

The first baseline run also caught a bug in the dataset itself (loss dates after the end of the policy period, so most claims were ineligible). `tests/test_dataset.py` now checks every label against the core rules: an eval is only as good as its labels.

**Next, to make the eval discriminate:**
- harder cases: an instruction hidden in the adjuster report rather than a claimant letter; contradictions that need arithmetic or two documents; genuinely ambiguous causes where `refer_to_human` is the right answer;
- documents written independently from the prompts;
- a red-team run against the allowlist (a document asking the analyst to call `record_decision`);
- an effort and model sweep (lower effort, Sonnet, Haiku) on the harder set, to find the cheapest setting that keeps zero false approvals.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest                                         # 28 tests, no API calls
.venv/bin/python -m claims_agent.evaluate --system baseline       # free
.venv/bin/python -m claims_agent.run CL-0011                      # one claim, live tool calls
.venv/bin/python -m claims_agent.evaluate --system agent --concurrency 3
```

The agent needs Claude credentials: `ANTHROPIC_API_KEY`, or a Claude Code login. Each run writes `runs/<timestamp>/`: `report.md`, `results.json`, a trace per claim (every tool call with the agent that made it) and the MCP server's audit log.

## Lessons

- **`setting_sources=[]`.** By default the SDK loads the user's and the project's Claude Code settings and `CLAUDE.md` files into the agent's context. In a product, that is someone's personal configuration leaking into your agent.
- **Subagents are a security boundary here, not a performance trick.** For one claim, a single agent would be fast enough. The reason to split is that the agent exposed to untrusted text cannot act.
- **`strict_mcp_config=True`.** Even with `setting_sources=[]`, the first live run received the claude.ai connectors of the logged-in account (dozens of unrelated tools) and stalled. Each run now checks the tool list the model actually received (`unexpected_tools` in the results), instead of trusting the configuration.
- **A refusal is information.** The core system returns refusals as data ("above the auto-approval limit; record refer_to_human"), and the orchestrator is told to read them and act on them.

## Layout

```
src/claims_agent/
  core.py         system of record and business rules
  core_server.py  MCP server (stdio) over core.py, with an audit log
  agent.py        orchestrator, subagents, options
  guardrails.py   PreToolUse allowlist per agent, PostToolUse trace
  dataset.py      36 synthetic claims with ground truth
  baseline.py     code-only baseline
  evaluate.py     runs and scores either system
  run.py          one claim, verbose
tests/            core rules, dataset labels, guardrails
```
