"""Optional contextual omissions never clear risk, temporal or claim dependencies."""

from datetime import date

import pytest
from legal_core.contracts import FactKey
from legal_core.intake import missing_facts_for
from legal_core.risk_engine import RiskLevel, evaluate_early_triage, evaluate_risk
from legal_core.verifier import (
    ClaimKind,
    ProposedClaim,
    VerificationResult,
    verify_claims,
)
from test_factual_safety_risk import facts
from test_guided_intake_v2 import _v2_facts
from test_risk_policy_v4 import v4_policy
from test_verifier import FRAGMENT_ID, _evidence, _supported_review


def without_optional_context(**screening):
    result = _v2_facts() | facts(**screening)
    for key in (FactKey.CONFLICT_STAGE, FactKey.CLINIC_ACTIONS):
        result.pop(key)
    return result


@pytest.mark.parametrize(
    ("amount", "level"),
    [(None, RiskLevel.LOW), (500_000, RiskLevel.MEDIUM), (1_000_000, RiskLevel.HIGH)],
)
def test_absent_context_allows_readiness_without_changing_v4_routing(amount, level):
    screening = ({"moneyRequested": "YES", "amount": {
        "amountKopecks": amount, "currency": "RUB",
    }} if amount is not None else {})
    supplied = without_optional_context(**screening)
    assert missing_facts_for(supplied) == []
    assert FactKey.CONFLICT_STAGE not in supplied and FactKey.CLINIC_ACTIONS not in supplied
    assert evaluate_risk(supplied, policy=v4_policy(), evidence_verified=True).level is level
    early = evaluate_early_triage(supplied, policy=v4_policy())
    assert (early is not None) == (level is RiskLevel.HIGH)
    if early is not None:
        assert early.reason_codes == ("HIGH_DEMAND_AMOUNT",)
    assert evaluate_risk(supplied, policy=v4_policy(), evidence_verified=False).level is (
        RiskLevel.UNAVAILABLE
    )


@pytest.mark.parametrize("key", [
    FactKey.INCOMING_COMMUNICATION, FactKey.INCOMING_SOURCE_STATUS,
    FactKey.SITUATION_AREAS, FactKey.EVENT_SUMMARY, FactKey.EVENT_DATE,
    FactKey.HEALTH_CONSEQUENCE_SIGNALS, FactKey.CASE_MATERIALS_STATUS,
    FactKey.AFFECTED_SERVICES,
])
def test_other_required_intake_facts_still_block_readiness(key):
    supplied = without_optional_context()
    supplied.pop(key)
    assert [item.fact_key for item in missing_facts_for(supplied)] == [key]


def test_unknown_date_and_safety_still_block_despite_optional_context():
    supplied = without_optional_context(hospitalizationReported="UNKNOWN")
    supplied[FactKey.EVENT_DATE] = {"date": None, "precision": "UNKNOWN"}
    assert [item.fact_key for item in missing_facts_for(supplied)] == [FactKey.EVENT_DATE]
    assessment = evaluate_risk(supplied, policy=v4_policy(), evidence_verified=True)
    assert assessment.level is RiskLevel.UNAVAILABLE
    assert assessment.reason_codes == ("FACTUAL_SAFETY_HOSPITALIZATIONREPORTED_UNKNOWN",)


@pytest.mark.parametrize("key", [FactKey.CONFLICT_STAGE, FactKey.CLINIC_ACTIONS])
def test_optional_readiness_does_not_verify_claim_depending_on_absent_context(key):
    supplied = without_optional_context()
    claim = ProposedClaim(
        claim_id="claim-1", kind=ClaimKind.ACTION,
        text="Synthetic action that depends on the clinic's confirmed context.",
        evidence_fragment_ids=(FRAGMENT_ID,), required_fact_keys=(key,),
    )
    result = verify_claims(
        [claim], evidence=[_evidence()], facts=supplied,
        as_of_date=date(2026, 9, 1), semantic_reviews=[_supported_review()],
    )
    assert result.analysis_allowed is False
    assert result.claims[0].result is VerificationResult.INSUFFICIENT_FACTS
    assert result.claims[0].reason_code == key.value + "_UNKNOWN"
    supplied[key] = "ONGOING" if key is FactKey.CONFLICT_STAGE else ["OFFERED_REFUND"]
    known = verify_claims(
        [claim], evidence=[_evidence()], facts=supplied,
        as_of_date=date(2026, 9, 1), semantic_reviews=[_supported_review()],
    )
    assert known.analysis_allowed is True
