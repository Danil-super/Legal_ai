"""A single service dependency rule shared by manual and interpreted answers."""

from copy import deepcopy

import pytest

from legal_core.intake_service_scope import invalidate_service_clarification_answers
from telegram_gateway.guided_case_intake_v2 import (
    invalidate_service_clarification_answers as gateway_invalidate,
)


def data():
    return {"intakeVersion": 2, "situationAreas": ["TREATMENT"],
            "affectedServices": ["Restoration", "Extraction"],
            "analysisClarificationRequests": [
                {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
                {"questionId": "RESPONSE_PURPOSE"},
            ], "analysisClarificationAnswers": {"answers": [
                {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "PAID"},
                {"questionId": "RESPONSE_PURPOSE", "value": "UNKNOWN"},
            ]}}


@pytest.mark.parametrize("services", [None, ["Extraction", "Restoration"], ["Restoration"]])
def test_changed_or_removed_service_scope_drops_only_service_scoped_questions_and_answers(services):
    original = data()
    before = deepcopy(original)
    expected = {**original, "analysisClarificationRequests": [
        {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None},
    ], "analysisClarificationAnswers": {"answers": [
        {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "UNKNOWN"},
    ]}}
    if services is None:
        expected.pop("affectedServices")
    else:
        expected["affectedServices"] = services
    assert invalidate_service_clarification_answers(original, services) == expected
    assert gateway_invalidate(original, services) == expected
    assert original == before


def test_unchanged_service_list_preserves_attempts_and_original_optional_key_representation():
    original = data()
    result = invalidate_service_clarification_answers(original, original["affectedServices"])
    assert result == original
    assert gateway_invalidate(original, original["affectedServices"]) == original


@pytest.mark.parametrize("services", [[], ["x"], ["Restoration", "Restoration"], ["x" * 501]])
def test_shared_legacy_service_validation_is_not_weakened(services):
    for helper in (invalidate_service_clarification_answers, gateway_invalidate):
        with pytest.raises(ValueError):
            helper(data(), services)


def test_legacy_500_character_service_limit_is_preserved():
    result = invalidate_service_clarification_answers({}, ["x" * 500])
    assert result["affectedServices"] == ["x" * 500]


def test_malformed_stored_request_list_is_not_promoted_to_new_questions():
    original = data()
    original["analysisClarificationRequests"] = [{"questionId": "RESPONSE_PURPOSE", "extra": True}]
    result = invalidate_service_clarification_answers(original, None)
    assert result["analysisClarificationRequests"] == []


def test_out_of_scope_stored_answers_still_fail_closed():
    original = data()
    original["analysisClarificationAnswers"]["answers"][0]["serviceSlot"] = 4
    with pytest.raises(ValueError):
        invalidate_service_clarification_answers(original, None)
