"""Code-only baseline: what a rules engine does without a model.

Same core system and same rules. The cause comes from keywords in the
claimant's description, the assessed loss from the adjuster report's
"Assessed loss" line. Whatever the core system refuses becomes a deny
(not covered) or a referral (any other refusal).

It shows where the model earns its cost: on the claims where the answer is
in free text, and nowhere else.
"""

from __future__ import annotations

import re

from claims_agent import core

KEYWORDS = [
    ("fire", ("fire", "smoke")),
    ("theft", ("stolen", "theft", "broke in")),
    ("flood", ("flood", "overflow", "stream", "river")),
    ("water_damage", ("pipe", "leak", "water")),
]


def peril_from_description(description: str) -> str | None:
    text = description.lower()
    for peril, words in KEYWORDS:
        if any(w in text for w in words):
            return peril
    return None


def assessed_loss(claim_id: str) -> float | None:
    for doc in core.list_documents(claim_id):
        if doc["type"] == "adjuster_report":
            text = core.read_document(claim_id, doc["doc_id"])["text"]
            match = re.search(r"Assessed loss: ILS ([\d,]+)", text)
            if match:
                return float(match.group(1).replace(",", ""))
    return None


def decide(claim_id: str) -> dict:
    """Records a decision through the core system (CLAIMS_DATA_DIR) and returns it."""
    eligibility = core.check_eligibility(claim_id)
    if not eligibility["eligible"]:
        return core.record_decision(claim_id, "deny", " ".join(eligibility["reasons"]))
    claim = core.get_claim(claim_id)
    peril = peril_from_description(claim["description"])
    loss = assessed_loss(claim_id)
    if peril is None or loss is None:
        return core.record_decision(claim_id, "refer_to_human", "Cause or assessed loss not found.")
    try:
        return core.record_decision(claim_id, "approve", f"Keyword rule: {peril}.", peril=peril, assessed_loss=loss)
    except core.CoreError as e:
        decision = "deny" if "not covered" in str(e) else "refer_to_human"
        return core.record_decision(claim_id, decision, str(e), peril=peril)
