"""The insurer's "core system": policies, claims, documents and decisions.

Everything here is deterministic code. The agents reach it only through the
MCP server (core_server.py), and every business invariant is enforced here,
not in a prompt: eligibility, coverage, payout arithmetic, the auto-approval
limit, required documents, and one decision per claim.

Storage is a directory of JSON files (CLAIMS_DATA_DIR), so each evaluation
run can work on its own throwaway copy.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

AUTO_APPROVAL_LIMIT = 25_000  # ILS: above this, a human must approve.
REPORTING_WINDOW_DAYS = 30
PERILS = ("water_damage", "fire", "theft", "flood")
DECISIONS = ("approve", "deny", "refer_to_human")
REQUIRED_DOCUMENTS = {"theft": "police_report"}


class CoreError(Exception):
    """A request the core system refuses. The message is returned to the caller."""


def data_dir() -> Path:
    default = Path(__file__).resolve().parents[2] / "data"
    return Path(os.environ.get("CLAIMS_DATA_DIR", default))


def _load(name: str) -> dict[str, Any]:
    path = data_dir() / name
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save(name: str, content: dict[str, Any]) -> None:
    # Write to a temp file, then rename: a crash never leaves half a file.
    target = data_dir() / name
    with tempfile.NamedTemporaryFile("w", dir=target.parent, delete=False, suffix=".tmp") as tmp:
        json.dump(content, tmp, indent=2, ensure_ascii=False)
    Path(tmp.name).replace(target)


def _claim(claim_id: str) -> dict[str, Any]:
    claims = _load("claims.json")
    if claim_id not in claims:
        raise CoreError(f"Unknown claim: {claim_id}")
    return claims[claim_id]


def get_policy(policy_id: str) -> dict[str, Any]:
    policies = _load("policies.json")
    if policy_id not in policies:
        raise CoreError(f"Unknown policy: {policy_id}")
    return policies[policy_id]


def get_claim(claim_id: str) -> dict[str, Any]:
    """Claim metadata. Document text is deliberately left out."""
    claim = _claim(claim_id)
    return {
        "claim_id": claim_id,
        "policy_id": claim["policy_id"],
        "date_of_loss": claim["date_of_loss"],
        "reported_on": claim["reported_on"],
        "claimed_amount": claim["claimed_amount"],
        "description": claim["description"],
        "document_count": len(claim["documents"]),
    }


def list_documents(claim_id: str) -> list[dict[str, str]]:
    return [
        {"doc_id": d["doc_id"], "type": d["type"], "title": d["title"]}
        for d in _claim(claim_id)["documents"]
    ]


def read_document(claim_id: str, doc_id: str) -> dict[str, str]:
    for d in _claim(claim_id)["documents"]:
        if d["doc_id"] == doc_id:
            return {"doc_id": doc_id, "type": d["type"], "title": d["title"], "text": d["text"]}
    raise CoreError(f"Claim {claim_id} has no document {doc_id}")


def check_eligibility(claim_id: str) -> dict[str, Any]:
    """Policy in force on the date of loss, premium paid, reported in time."""
    claim = _claim(claim_id)
    policy = get_policy(claim["policy_id"])
    loss = date.fromisoformat(claim["date_of_loss"])
    reported = date.fromisoformat(claim["reported_on"])
    reasons = []
    if not date.fromisoformat(policy["start"]) <= loss <= date.fromisoformat(policy["end"]):
        reasons.append("The date of loss is outside the policy period.")
    if loss > date.fromisoformat(policy["premium_paid_through"]):
        reasons.append(
            f"The policy had lapsed: premium paid through {policy['premium_paid_through']}, "
            f"loss on {claim['date_of_loss']}."
        )
    delay = (reported - loss).days
    if delay > REPORTING_WINDOW_DAYS:
        reasons.append(
            f"Reported {delay} days after the loss; the policy requires {REPORTING_WINDOW_DAYS}."
        )
    return {
        "claim_id": claim_id,
        "policy_id": claim["policy_id"],
        "eligible": not reasons,
        "reasons": reasons,
    }


def compute_payout(policy_id: str, peril: str, assessed_loss: float) -> dict[str, Any]:
    """Payout = min(assessed loss, limit) - deductible, never below zero."""
    if peril not in PERILS:
        raise CoreError(f"Unknown peril {peril!r}; expected one of {', '.join(PERILS)}")
    if assessed_loss < 0:
        raise CoreError("assessed_loss must be positive")
    coverage = get_policy(policy_id)["coverages"].get(peril)
    if coverage is None:
        return {"policy_id": policy_id, "peril": peril, "covered": False, "payout": 0}
    payout = compute_payout_for({peril: coverage}, peril, assessed_loss)
    return {
        "policy_id": policy_id,
        "peril": peril,
        "covered": True,
        "limit": coverage["limit"],
        "deductible": coverage["deductible"],
        "payout": payout,
        "requires_human_approval": payout > AUTO_APPROVAL_LIMIT,
    }


def compute_payout_for(coverages: dict[str, Any], peril: str, assessed_loss: float) -> int:
    coverage = coverages[peril]
    return round(max(0, min(assessed_loss, coverage["limit"]) - coverage["deductible"]))


def record_decision(
    claim_id: str,
    decision: str,
    rationale: str,
    peril: str | None = None,
    assessed_loss: float | None = None,
) -> dict[str, Any]:
    """Write the decision to the system of record.

    The caller never sends an amount: for an approval it sends the peril and
    the adjuster's assessed loss, and the payout is computed here.
    """
    if decision not in DECISIONS:
        raise CoreError(f"decision must be one of {', '.join(DECISIONS)}")
    if not rationale.strip():
        raise CoreError("A rationale is required.")
    claim = _claim(claim_id)
    decisions = _load("decisions.json")
    if claim_id in decisions:
        raise CoreError(f"A decision is already recorded for {claim_id}; it cannot be changed here.")

    record: dict[str, Any] = {"decision": decision, "rationale": rationale.strip()}
    if decision == "approve":
        if peril is None or assessed_loss is None:
            raise CoreError("An approval needs the peril and the adjuster's assessed loss.")
        eligibility = check_eligibility(claim_id)
        if not eligibility["eligible"]:
            raise CoreError("Not eligible: " + " ".join(eligibility["reasons"]))
        payout = compute_payout(claim["policy_id"], peril, assessed_loss)
        if not payout["covered"]:
            raise CoreError(f"Peril {peril} is not covered by policy {claim['policy_id']}.")
        required = REQUIRED_DOCUMENTS.get(peril)
        if required and required not in {d["type"] for d in claim["documents"]}:
            raise CoreError(
                f"A {peril} claim needs a {required} before approval; record refer_to_human."
            )
        if payout["requires_human_approval"]:
            raise CoreError(
                f"Payout ILS {payout['payout']:,} is above the auto-approval limit of "
                f"ILS {AUTO_APPROVAL_LIMIT:,}; record refer_to_human."
            )
        record.update(peril=peril, assessed_loss=assessed_loss, payout=payout["payout"])
    elif peril is not None:
        record["peril"] = peril

    decisions[claim_id] = record
    _save("decisions.json", decisions)
    return {"claim_id": claim_id, "recorded": True, **record}


def recorded_decision(claim_id: str) -> dict[str, Any] | None:
    return _load("decisions.json").get(claim_id)
