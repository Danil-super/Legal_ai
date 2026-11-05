"""A legacy legal assessment of a service cannot use a later verified copy."""

from datetime import date

import pytest

from legal_core.contracts import FactKey
from legal_core.verifier import ClaimKind, ProposedClaim, VerificationResult, verify_claims
from test_verifier import FRAGMENT_ID, _evidence, _supported_review


@pytest.mark.parametrize("service_date,expected", [
    ("2026-09-01", VerificationResult.NOT_APPLICABLE),
    ("2026-10-08", VerificationResult.VERIFIED),
    ("2026-10-14", VerificationResult.VERIFIED),
    ({"date": "2026-10-14", "precision": "EXACT"}, VerificationResult.VERIFIED),
    (date(2026, 10, 14), VerificationResult.VERIFIED),
    ({"date": "2026-10-14", "precision": "APPROXIMATE"},
     VerificationResult.INSUFFICIENT_FACTS),
    ("неизвестная дата", VerificationResult.INSUFFICIENT_FACTS),
    ("20261014", VerificationResult.INSUFFICIENT_FACTS),
])
def test_legacy_retrospective_legal_claim_requires_effective_service_basis(service_date, expected):
    claim = ProposedClaim(
        claim_id="claim-1", kind=ClaimKind.LEGAL,
        text="На дату оказания синтетической услуги применялось это правило.",
        evidence_fragment_ids=(FRAGMENT_ID,),
        required_fact_keys=(FactKey.SERVICE_TYPE, FactKey.SERVICE_DATE),
    )
    result = verify_claims(
        [claim], facts={FactKey.SERVICE_TYPE: "Синтетическая услуга",
                        FactKey.SERVICE_DATE: service_date},
        as_of_date=date(2026, 10, 14),
        evidence=[_evidence(effective_from=date(2026, 10, 8))],
        semantic_reviews=[_supported_review()],
    )
    assert result.claims[0].result is expected
    assert result.analysis_allowed is (expected is VerificationResult.VERIFIED)


def test_legacy_current_organizational_action_keeps_case_date_basis():
    claim = ProposedClaim(
        claim_id="claim-1", kind=ClaimKind.ACTION,
        text="Пригласить пациента для внутренней проверки текущего обращения.",
        evidence_fragment_ids=(FRAGMENT_ID,),
        required_fact_keys=(FactKey.SERVICE_DATE,),
    )
    result = verify_claims(
        [claim], facts={FactKey.SERVICE_DATE: "2026-09-01"},
        as_of_date=date(2026, 10, 14),
        evidence=[_evidence(effective_from=date(2026, 10, 8))],
        semantic_reviews=[_supported_review()],
    )
    assert result.analysis_allowed
