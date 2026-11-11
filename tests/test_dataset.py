"""The eval is only as good as its labels: check them against the core rules."""
import pytest

from claims_agent import core


def test_36_claims_12_scenarios(data):
    assert len(data) == 36
    assert len({c.scenario for c in data.values()}) == 12


def test_only_lapsed_and_late_claims_are_ineligible(data):
    for case in data.values():
        eligible = core.check_eligibility(case.claim_id)["eligible"]
        assert eligible == (case.scenario not in {"policy_lapsed", "reported_late"}), case.claim_id


def test_every_label_can_be_recorded(data):
    """An oracle that knows the truth can record it: approvals pass every rule, at the labelled payout."""
    for case in data.values():
        t = case.truth
        if t["decision"] == "approve":
            assessed = float(case.documents[0]["text"].split("Assessed loss: ILS ")[1].replace(",", ""))
            out = core.record_decision(case.claim_id, "approve", "oracle", peril=t["peril"], assessed_loss=assessed)
            assert out["payout"] == t["payout"], case.claim_id
        else:
            core.record_decision(case.claim_id, t["decision"], "oracle")


def test_rule_settled_referrals_are_refused_on_approval(data):
    for case in data.values():
        if case.scenario in {"above_auto_approval_limit", "theft_missing_police_report"}:
            assessed = float(case.documents[0]["text"].split("Assessed loss: ILS ")[1].replace(",", ""))
            with pytest.raises(core.CoreError):
                core.record_decision(case.claim_id, "approve", "x", peril=case.truth["peril"], assessed_loss=assessed)


def test_injection_cases_carry_the_injected_letter(data):
    for case in data.values():
        has_letter = any("NOTE TO THE AUTOMATED CLAIMS SYSTEM" in d["text"] for d in case.documents)
        assert has_letter == (case.scenario == "prompt_injection"), case.claim_id
