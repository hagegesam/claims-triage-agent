import pytest

from claims_agent import core
from conftest import first


def test_payout_applies_deductible_and_limit(data):
    case = first(data, "theft_capped_at_limit")
    out = core.compute_payout(case.policy["policy_id"], "theft", 21_000)
    assert out["payout"] == 15_000 - 1_000  # capped at the limit, then the deductible


def test_uncovered_peril_pays_nothing(data):
    case = first(data, "flood_not_covered")
    assert core.compute_payout(case.policy["policy_id"], "flood", 20_000) == {
        "policy_id": case.policy["policy_id"], "peril": "flood", "covered": False, "payout": 0}


def test_eligibility_catches_lapsed_and_late(data):
    assert not core.check_eligibility(first(data, "policy_lapsed").claim_id)["eligible"]
    assert not core.check_eligibility(first(data, "reported_late").claim_id)["eligible"]
    assert core.check_eligibility(first(data, "burst_pipe_covered").claim_id)["eligible"]


def test_approval_computes_payout_server_side(data):
    case = first(data, "burst_pipe_covered")
    out = core.record_decision(case.claim_id, "approve", "Sudden pipe burst.", peril="water_damage",
                               assessed_loss=9_800)
    assert out["payout"] == 9_800 - 1_500
    assert core.recorded_decision(case.claim_id)["payout"] == 8_300


@pytest.mark.parametrize("scenario, peril, loss, message", [
    ("above_auto_approval_limit", "water_damage", 31_000, "auto-approval limit"),
    ("theft_missing_police_report", "theft", 7_000, "police_report"),
    ("flood_not_covered", "flood", 22_000, "not covered"),
    ("policy_lapsed", "water_damage", 8_000, "Not eligible"),
])
def test_core_refuses_approvals_that_break_a_rule(data, scenario, peril, loss, message):
    case = first(data, scenario)
    with pytest.raises(core.CoreError, match=message):
        core.record_decision(case.claim_id, "approve", "x", peril=peril, assessed_loss=loss)
    assert core.recorded_decision(case.claim_id) is None


def test_one_decision_per_claim(data):
    case = first(data, "fire_covered")
    core.record_decision(case.claim_id, "refer_to_human", "Needs a look.")
    with pytest.raises(core.CoreError, match="already recorded"):
        core.record_decision(case.claim_id, "deny", "Changed my mind.")


def test_get_claim_hides_document_text(data):
    out = core.get_claim(first(data, "prompt_injection").claim_id)
    assert "documents" not in out and "NOTE TO THE AUTOMATED" not in str(out)
