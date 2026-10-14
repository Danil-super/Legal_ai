"""Typed unknown/date boundaries for explicitly service-dependent synthetic claims."""

from dataclasses import replace
from datetime import date

import pytest

from legal_core.contracts import FactKey
from legal_core.verifier import ClaimKind, ProposedClaim, VerificationResult, verify_claims
from test_verifier import FRAGMENT_ID, _evidence, _supported_review


def _facts(*, precision: str = "EXACT", provided_date: str | None = "2026-10-01") -> dict:
    return {
        FactKey.INTAKE_VERSION: "GUIDED_V2", FactKey.SITUATION_AREAS: ["TREATMENT"],
        FactKey.AFFECTED_SERVICES: ["Синтетическая услуга"],
        FactKey.EVENT_DATE: {"date": "2026-10-14", "precision": "EXACT"},
        FactKey.ANALYSIS_CLARIFICATION_ANSWERS: {"answers": [
            {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "PAID"},
            {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
             "value": {"date": provided_date, "precision": precision}},
            {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "ANSWER_REQUEST"},
        ]},
    }


def _verify(facts: dict, *, starts: date = date(2026, 9, 1), current_copy: bool = False,
            non_service_action: bool = False):
    claim = ProposedClaim(
        claim_id="claim-1", kind=ClaimKind.ACTION if non_service_action else ClaimKind.LEGAL,
        text="Синтетическое действие с обращением." if non_service_action
        else "Синтетический вывод об услуге.",
        evidence_fragment_ids=(FRAGMENT_ID,),
        required_fact_keys=(FactKey.ANALYSIS_CLARIFICATION_ANSWERS,),
    )
    evidence = replace(_evidence(effective_from=starts),
                       date_basis="LAWYER_CURRENT_COPY" if current_copy else "DATED_EDITION")
    return verify_claims([claim], evidence=[evidence], facts=facts,
                         as_of_date=date(2026, 10, 14), semantic_reviews=[_supported_review()])


def test_known_global_null_slot_is_metadata_not_an_unknown_answer() -> None:
    result = _verify(_facts())
    assert result.analysis_allowed
    assert result.claims[0].result is VerificationResult.VERIFIED


@pytest.mark.parametrize("precision,provided_date", [
    ("UNKNOWN", None), ("APPROXIMATE", "2026-10-01"),
])
def test_unknown_or_approximate_service_date_cannot_satisfy_declared_dependency(
    precision: str, provided_date: str | None,
) -> None:
    result = _verify(_facts(precision=precision, provided_date=provided_date))
    assert not result.analysis_allowed
    assert result.claims[0].result is VerificationResult.INSUFFICIENT_FACTS


@pytest.mark.parametrize("current_copy", [False, True])
def test_later_only_evidence_never_grounds_an_earlier_service(current_copy: bool) -> None:
    facts = _facts()
    # Omit optional global null to expose the temporal leak separately from metadata handling.
    facts[FactKey.ANALYSIS_CLARIFICATION_ANSWERS]["answers"].pop()
    result = _verify(facts, starts=date(2026, 10, 8), current_copy=current_copy)
    assert not result.analysis_allowed
    assert result.claims[0].result is VerificationResult.NOT_APPLICABLE
    assert result.claims[0].reason_code == "EVIDENCE_NOT_EFFECTIVE_ON_SERVICE_DATE"


def test_event_date_does_not_fill_in_a_missing_service_date() -> None:
    facts = _facts()
    facts[FactKey.ANALYSIS_CLARIFICATION_ANSWERS]["answers"] = [
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "PAID"},
    ]
    result = _verify(facts)
    assert not result.analysis_allowed
    assert result.claims[0].reason_code == "SERVICE_DATE_BINDING_UNKNOWN"


def test_one_service_date_is_not_assigned_to_all_affected_services() -> None:
    facts = _facts()
    facts[FactKey.AFFECTED_SERVICES].append("Другая синтетическая услуга")
    facts[FactKey.ANALYSIS_CLARIFICATION_ANSWERS]["answers"].pop()
    result = _verify(facts)
    assert not result.analysis_allowed
    assert result.claims[0].reason_code == "SERVICE_DATE_BINDING_UNKNOWN"


def test_global_purpose_only_can_ground_non_service_actions() -> None:
    facts = _facts()
    facts[FactKey.ANALYSIS_CLARIFICATION_ANSWERS]["answers"] = [
        {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "RECEIVE_AND_EXPLAIN"},
    ]
    assert _verify(facts, non_service_action=True).analysis_allowed


def test_global_purpose_does_not_prove_binding_for_a_treatment_legal_claim() -> None:
    facts = _facts()
    facts[FactKey.ANALYSIS_CLARIFICATION_ANSWERS]["answers"] = [
        {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "RECEIVE_AND_EXPLAIN"},
    ]
    result = _verify(facts)
    assert not result.analysis_allowed
    assert result.claims[0].reason_code == "SERVICE_DATE_BINDING_UNKNOWN"


@pytest.mark.parametrize("bad", [
    "UNKNOWN", False, "SYNTHETIC_CANARY", {"date": "2026-10-01", "precision": "EXACT"},
])
def test_wrong_typed_payment_answer_cannot_satisfy_dependency(bad: object) -> None:
    facts = _facts()
    facts[FactKey.ANALYSIS_CLARIFICATION_ANSWERS]["answers"][0]["value"] = bad
    assert not _verify(facts).analysis_allowed
