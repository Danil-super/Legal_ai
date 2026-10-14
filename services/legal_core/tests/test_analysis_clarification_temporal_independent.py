"""Independent all-service temporal regressions using fixed fictional facts."""

from dataclasses import replace
from datetime import date

import pytest

from legal_core.contracts import FactKey
from legal_core.verifier import ClaimKind, ProposedClaim, VerificationResult, verify_claims
from test_verifier import FRAGMENT_ID, _evidence, _supported_review


@pytest.mark.parametrize("service_count,earlier_slot", [(2, 1), (5, 4)])
@pytest.mark.parametrize("date_basis", ["DATED_EDITION", "LAWYER_CURRENT_COPY"])
def test_every_confirmed_service_date_must_be_supported_by_evidence(
    service_count: int, earlier_slot: int, date_basis: str,
) -> None:
    facts = {
        FactKey.INTAKE_VERSION: "GUIDED_V2",
        FactKey.SITUATION_AREAS: ["TREATMENT"],
        FactKey.AFFECTED_SERVICES: [
            f"Синтетическая услуга {slot}" for slot in range(service_count)
        ],
        FactKey.EVENT_DATE: {"date": "2026-10-14", "precision": "EXACT"},
        FactKey.ANALYSIS_CLARIFICATION_ANSWERS: {"answers": [
            {
                "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": slot,
                "value": {
                    "date": "2026-10-01" if slot == earlier_slot else "2026-10-10",
                    "precision": "EXACT",
                },
            }
            for slot in range(service_count)
        ]},
    }
    claim = ProposedClaim(
        claim_id="claim-1", kind=ClaimKind.LEGAL, text="Синтетический вывод об услугах.",
        evidence_fragment_ids=(FRAGMENT_ID,),
        required_fact_keys=(FactKey.ANALYSIS_CLARIFICATION_ANSWERS,),
    )
    evidence = replace(
        _evidence(effective_from=date(2026, 10, 8)), date_basis=date_basis,
    )

    decision = verify_claims(
        [claim], evidence=[evidence], facts=facts, as_of_date=date(2026, 10, 14),
        semantic_reviews=[_supported_review()],
    )

    assert not decision.analysis_allowed
    assert decision.claims[0].result is VerificationResult.NOT_APPLICABLE
    assert decision.claims[0].reason_code == "EVIDENCE_NOT_EFFECTIVE_ON_SERVICE_DATE"
