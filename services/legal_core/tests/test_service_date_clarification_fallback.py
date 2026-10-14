"""A proven missing date binding becomes a question, never an inferred fact."""
from copy import deepcopy

import pytest

from legal_core import analysis_clarifications as subject
from legal_core.analysis_clarifications import ClarificationRequest


def facts():
    return {"INTAKE_VERSION": "GUIDED_V2", "SITUATION_AREAS": ["TREATMENT"],
            "AFFECTED_SERVICES": ["Синтетическая услуга"],
            "EVENT_DATE": {"date": "2026-10-14", "precision": "EXACT"},
            "EVENT_SUMMARY": "Синтетическая услуга оказана 14.10.2026."}


def request(slot=0):
    return ClarificationRequest(questionId="SERVICE_PROVIDED_DATE", serviceSlot=slot)


def complete(data, requests=(), *, gap=True):
    return subject.complete_service_date_requests(list(requests), data,
                                                 service_date_binding_unknown=gap)


def test_actual_date_binding_gap_requires_slot_confirmation_even_with_narrative_date():
    data = facts()
    original = deepcopy(data)
    assert complete(data) == [request()]
    assert data == original
    assert complete(data, gap=False) == []


@pytest.mark.parametrize("precision,day", [
    ("EXACT", "2026-10-14"), ("APPROXIMATE", "2026-10-14"), ("UNKNOWN", None),
])
def test_any_attempted_date_is_not_asked_again(precision, day):
    data = facts() | {"ANALYSIS_CLARIFICATION_ANSWERS": {"answers": [{
        "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
        "value": {"date": day, "precision": precision},
    }]}}
    assert complete(data) == []


def test_preserves_existing_questions_without_duplicate_and_caps_total_at_three():
    data = facts() | {"AFFECTED_SERVICES": [f"Услуга {slot}" for slot in range(5)]}
    payment = ClarificationRequest(questionId="SERVICE_PAYMENT_BASIS", serviceSlot=0)
    assert complete(data, [payment, request()]) == [payment, request(), request(1)]
    assert complete(data) == [request(0), request(1), request(2)]
    data["ANALYSIS_CLARIFICATION_ANSWERS"] = {"answers": [{
        "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": slot,
        "value": {"date": "2026-10-14", "precision": "EXACT"},
    } for slot in range(3)]}
    assert complete(data) == [request(3), request(4)]
    existing = [payment, request(3), request(4)]
    assert complete(data, existing) == existing


@pytest.mark.parametrize("change", [
    {"INTAKE_VERSION": "GUIDED_V1"}, {"SITUATION_AREAS": ["SERVICE"]},
    {"AFFECTED_SERVICES": []}, {"AFFECTED_SERVICES": ["x"]},
    {"AFFECTED_SERVICES": ["Услуга"] * 6},
    {"ANALYSIS_CLARIFICATION_ANSWERS": {"answers": "malformed"}},
])
def test_unsupported_or_corrupt_frozen_scope_never_generates_questions(change):
    assert complete(facts() | change) == []


def test_invalid_model_request_cannot_be_repaired_or_silently_dropped():
    with pytest.raises(ValueError):
        complete(facts(), [request(1)])
