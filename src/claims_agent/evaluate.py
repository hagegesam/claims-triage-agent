"""Run the agent or the baseline on labelled claims and score the decisions.

The score reads the decision from the system of record (decisions.json),
not from what the agent says it did. Each claim runs on its own copy of
the data, so runs are independent.

  python -m claims_agent.evaluate --system baseline
  python -m claims_agent.evaluate --system agent --limit 12 --concurrency 3
  python -m claims_agent.evaluate --system agent --claims CL-0004 CL-0011
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from claims_agent import baseline, core

ROOT = Path(__file__).resolve().parents[2]


def fresh_data_dir(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for name in ("policies.json", "claims.json"):
        shutil.copy(core.data_dir() / name, target / name)
    (target / "decisions.json").write_text("{}")
    return target


def score(claim_id: str, truth: dict[str, Any], decision: dict[str, Any] | None) -> dict[str, Any]:
    got = decision["decision"] if decision else "none"
    row = {
        "claim_id": claim_id,
        "scenario": truth["scenario"],
        "needs_model": truth["needs_model"],
        "expected": truth["decision"],
        "got": got,
        "decision_correct": got == truth["decision"],
        "expected_payout": truth["payout"],
        "payout": decision.get("payout") if decision else None,
    }
    row["correct"] = row["decision_correct"] and row["payout"] == truth["payout"]
    row["false_approval"] = got == "approve" and truth["decision"] != "approve"
    return row


def run_baseline(claim_ids: list[str], run_dir: Path) -> list[dict[str, Any]]:
    out = []
    for claim_id in claim_ids:
        os.environ["CLAIMS_DATA_DIR"] = str(fresh_data_dir(run_dir / "data" / claim_id))
        decision = baseline.decide(claim_id)
        out.append({"claim_id": claim_id, "decision": decision, "cost_usd": 0.0, "duration_s": 0.0})
    os.environ.pop("CLAIMS_DATA_DIR", None)
    return out


async def run_agent(claim_ids: list[str], run_dir: Path, args: argparse.Namespace) -> list[dict[str, Any]]:
    from claims_agent.agent import run_claim

    gate = asyncio.Semaphore(args.concurrency)

    async def one(claim_id: str) -> dict[str, Any]:
        async with gate:
            data = fresh_data_dir(run_dir / "data" / claim_id)
            r = await run_claim(claim_id, data, run_dir / "traces", model=args.model,
                                effort=args.effort, budget_usd=args.budget_per_claim)
            print(f"{claim_id}: {(r.decision or {}).get('decision', 'none'):<15} "
                  f"${r.cost_usd or 0:.3f} {r.duration_s}s{'  ERROR ' + r.error if r.error else ''}", flush=True)
            return {**r.__dict__}

    return await asyncio.gather(*(one(c) for c in claim_ids))


def _rate(rows: list[dict[str, Any]]) -> float | None:
    """Share of correct rows; None when there is nothing to measure (not 0.0)."""
    return round(sum(r["correct"] for r in rows) / len(rows), 3) if rows else None


def summarise(rows: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    by_scenario: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        by_scenario[r["scenario"]].append(r["correct"])
    costs = [r["cost_usd"] for r in runs if r.get("cost_usd") is not None]
    durations = sorted(r["duration_s"] for r in runs)
    model_rows = [r for r in rows if r["needs_model"]]
    rule_rows = [r for r in rows if not r["needs_model"]]
    return {
        "claims": n,
        "accuracy": round(sum(r["correct"] for r in rows) / n, 3),
        "accuracy_needs_model": _rate(model_rows),
        "accuracy_rule_settled": _rate(rule_rows),
        "false_approvals": sum(r["false_approval"] for r in rows),
        "no_decision": sum(r["got"] == "none" for r in rows),
        "confusion": {f"{e} -> {g}": c for (e, g), c in sorted(Counter((r["expected"], r["got"]) for r in rows).items())},
        "by_scenario": {s: f"{sum(v)}/{len(v)}" for s, v in sorted(by_scenario.items())},
        "cost_total_usd": round(sum(costs), 3),
        "cost_per_claim_usd": round(statistics.mean(costs), 3) if costs else None,
        "latency_p50_s": durations[n // 2] if durations else None,
        "latency_max_s": durations[-1] if durations else None,
        "guardrail_denials": sum(len(r.get("denials", [])) for r in runs),
        "errors": [f"{r['claim_id']}: {r['error']}" for r in runs if r.get("error")],
    }


def report_md(system: str, summary: dict[str, Any], rows: list[dict[str, Any]], args: argparse.Namespace) -> str:
    lines = [f"# Eval: {system}", ""]
    if system == "agent":
        lines.append(f"Model `{args.model}`, effort `{args.effort}`.")
        lines.append("")
    for key in ("claims", "accuracy", "accuracy_needs_model", "accuracy_rule_settled", "false_approvals",
                "no_decision", "cost_total_usd", "cost_per_claim_usd", "latency_p50_s", "latency_max_s",
                "guardrail_denials"):
        lines.append(f"- **{key}**: {summary[key]}")
    lines += ["", "## By scenario", "", "| Scenario | Correct |", "|---|---|"]
    lines += [f"| {s} | {v} |" for s, v in summary["by_scenario"].items()]
    lines += ["", "## Confusion (expected -> got)", ""]
    lines += [f"- {k}: {v}" for k, v in summary["confusion"].items()]
    wrong = [r for r in rows if not r["correct"]]
    if wrong:
        lines += ["", "## Errors to read", "", "| Claim | Scenario | Expected | Got | Payout (exp / got) |", "|---|---|---|---|---|"]
        lines += [f"| {r['claim_id']} | {r['scenario']} | {r['expected']} | {r['got']} | {r['expected_payout']} / {r['payout']} |" for r in wrong]
    if summary["errors"]:
        lines += ["", "## Run errors", ""] + [f"- {e}" for e in summary["errors"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--system", choices=["agent", "baseline"], required=True)
    p.add_argument("--claims", nargs="*")
    p.add_argument("--scenario")
    p.add_argument("--limit", type=int)
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--budget-per-claim", type=float, default=2.0)
    args = p.parse_args()

    labels = json.loads((core.data_dir() / "labels.json").read_text())
    claim_ids = args.claims or sorted(labels)
    if args.scenario:
        claim_ids = [c for c in claim_ids if labels[c]["scenario"] == args.scenario]
    if args.limit:
        claim_ids = claim_ids[: args.limit]

    run_dir = ROOT / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.system}"
    if args.system == "baseline":
        runs = run_baseline(claim_ids, run_dir)
    else:
        runs = asyncio.run(run_agent(claim_ids, run_dir, args))

    rows = [score(r["claim_id"], labels[r["claim_id"]], r["decision"]) for r in runs]
    summary = summarise(rows, runs)
    (run_dir / "results.json").write_text(json.dumps({"summary": summary, "rows": rows, "runs": runs}, indent=2, default=str))
    (run_dir / "report.md").write_text(report_md(args.system, summary, rows, args))
    print(report_md(args.system, summary, rows, args))
    print(f"Saved to {run_dir}")


if __name__ == "__main__":
    main()
