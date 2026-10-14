"""Supported service-dependent actions cannot evade the unchanged date gate."""
from datetime import date

import pytest

from legal_core.analysis_api import _verified_action_items
from legal_core.contracts import FactKey
from legal_core.verifier import ClaimKind, ProposedClaim, VerificationResult, verify_claims
from test_verifier import FRAGMENT_ID, _evidence, _supported_review


@pytest.mark.parametrize("precision,day,expected", [
    (None, None, VerificationResult.INSUFFICIENT_FACTS),
    ("UNKNOWN", None, VerificationResult.INSUFFICIENT_FACTS),
    ("APPROXIMATE", "2026-10-14", VerificationResult.INSUFFICIENT_FACTS),
    ("EXACT", "2026-10-01", VerificationResult.NOT_APPLICABLE),
    ("EXACT", "2026-10-14", VerificationResult.VERIFIED),
])
def test_service_dependent_action_plan_retains_unknown_and_historical_date_denial(
    precision, day, expected,
):
    answers = [] if precision is None else [{
        "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
        "value": {"date": day, "precision": precision},
    }]
    facts = {
        FactKey.INTAKE_VERSION: "GUIDED_V2", FactKey.SITUATION_AREAS: ["TREATMENT"],
        FactKey.AFFECTED_SERVICES: ["Синтетическая услуга"],
        FactKey.EVENT_DATE: {"date": "2026-10-14", "precision": "EXACT"},
        FactKey.ANALYSIS_CLARIFICATION_ANSWERS: {"answers": answers},
    }
    claim = ProposedClaim(
        claim_id="claim-1", kind=ClaimKind.ACTION, text="Синтетический шаг по услуге.",
        evidence_fragment_ids=(FRAGMENT_ID,), required_fact_keys=(FactKey.AFFECTED_SERVICES,),
    )
    result = verify_claims(
        [claim], facts=facts, as_of_date=date(2026, 10, 14),
        evidence=[_evidence(effective_from=date(2026, 10, 8))],
        semantic_reviews=[_supported_review()],
    )
    assert result.claims[0].result is expected
    assert result.analysis_allowed is (expected is VerificationResult.VERIFIED)
    actions = _verified_action_items((claim,), {claim.claim_id: result.claims[0].result})
    expected_actions = [claim.text] if expected is VerificationResult.VERIFIED else []
    assert actions == expected_actions


def test_legacy_service_action_uses_real_legacy_dependencies_not_v2_bundle():
    facts = {
        FactKey.SERVICE_TYPE: "Синтетическая услуга",
        FactKey.SERVICE_DATE: "2026-10-14",
    }
    claim = ProposedClaim(
        claim_id="claim-1", kind=ClaimKind.ACTION, text="Синтетический шаг по услуге.",
        evidence_fragment_ids=(FRAGMENT_ID,),
        required_fact_keys=(FactKey.SERVICE_TYPE, FactKey.SERVICE_DATE),
    )
    result = verify_claims(
        [claim], facts=facts, as_of_date=date(2026, 10, 14),
        evidence=[_evidence(effective_from=date(2026, 10, 8))],
        semantic_reviews=[_supported_review()],
    )
    assert result.claims[0].result is VerificationResult.VERIFIED
    assert result.analysis_allowed
    actions = _verified_action_items((claim,), {claim.claim_id: result.claims[0].result})
    assert actions == [claim.text]
