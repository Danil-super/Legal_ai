"""Manual service labels must remain representable by the existing Core fact contract."""

from copy import deepcopy

import pytest
from legal_core.api_contracts import FactInput
from telegram_gateway.guided_case_intake_v2 import (
    apply_clarification_answer,
    invalidate_service_clarification_answers,
    next_clarification_request,
    next_missing_state,
    parse_answer,
)
from test_intake_clarification_preflight import complete_treatment_input


@pytest.mark.parametrize("character", ["я", "🦷"])
def test_manual_service_at_core_character_limit_is_preserved_in_fact_input(character):
    label = character * 120
    services = parse_answer("affectedServices", label)
    fact = FactInput.model_validate({
        "factKey": "AFFECTED_SERVICES", "valueType": "TEXT_LIST", "value": {"items": services},
        "sourceType": "USER_STATEMENT",
    })
    assert fact.value == {"items": [label]}


@pytest.mark.parametrize("length", [121, 500])
@pytest.mark.parametrize("character", ["я", "🦷"])
def test_manual_service_longer_than_core_contract_requires_user_correction(length, character):
    with pytest.raises(ValueError, match="120"):
        parse_answer("affectedServices", character * length)


@pytest.mark.parametrize("length", [121, 500])
@pytest.mark.parametrize("character", ["я", "🦷"])
def test_retained_overlong_service_reopens_service_edit_without_mutating_or_skipping_facts(
    length, character,
):
    data = complete_treatment_input()
    data["affectedServices"] = [character * length]
    data["analysisClarificationAnswers"] = {"answers": [{
        "questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "UNKNOWN",
    }]}
    original = deepcopy(data)
    assert next_missing_state(data) == "SERVICES"
    assert data == original

    corrected = invalidate_service_clarification_answers(data, ["Синтетическая услуга"])
    assert corrected["analysisClarificationAnswers"] == data["analysisClarificationAnswers"]
    seen = set()
    for _ in range(2):
        assert next_missing_state(corrected) == "ANALYSIS_CLARIFICATION"
        request = next_clarification_request(corrected)
        seen.add(request.question_id)
        corrected = apply_clarification_answer(corrected, request, "UNKNOWN")
    assert seen == {"SERVICE_PAYMENT_BASIS", "SERVICE_PROVIDED_DATE"}
    assert next_missing_state(corrected) == "SUMMARY"
    assert data == original
