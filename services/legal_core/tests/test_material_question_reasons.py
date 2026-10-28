"""Question explanations cannot grant routing, answers, or new source identities."""

from copy import deepcopy

import pytest

from legal_core import intake_answer_contracts as contracts


def question(state, **data):
    return contracts.derive_answer_question({"intakeVersion": 2, **data}, state)


def test_applicability_and_action_questions_preserve_unknowns_and_legacy_wire():
    data = {"intakeVersion": 2, "situationAreas": ["TREATMENT"],
            "affectedServices": ["Синтетическая установка коронки"],
            "analysisClarificationRequests": [
                {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0},
                {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None},
            ]}
    original = deepcopy(data)
    current = contracts.derive_answer_question(data, "ANALYSIS_CLARIFICATION")
    wire, digest = current.model_dump(mode="json", by_alias=True), current.question_sha256
    explanation = contracts.material_question(current)
    assert explanation.reason_code == "SOURCE_APPLICABILITY"
    assert explanation.question.model_dump(mode="json", by_alias=True) == wire
    assert explanation.question.question_sha256 == digest
    data["analysisClarificationAnswers"] = {"answers": [{
        "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
        "value": {"date": None, "precision": "UNKNOWN"},
    }]}
    purpose = contracts.derive_answer_question(data, "ANALYSIS_CLARIFICATION")
    assert purpose.clarification_request.question_id == "RESPONSE_PURPOSE"
    assert contracts.material_question(purpose).reason_code == "ACTION_SELECTION"
    assert data["analysisClarificationAnswers"]["answers"][0]["value"]["date"] is None
    data.pop("analysisClarificationAnswers")
    assert data == original


@pytest.mark.parametrize("state", ["INCOMING", "HEALTH", "SAFETY"])
def test_signal_questions_explain_safety_without_deciding_the_signal(state):
    current = question(state)
    result = contracts.material_question(current)
    assert result.reason_code == "SAFETY_DECISION"
    assert result.question == current
    assert not {"value", "riskLevel", "confirmed"} & result.model_dump().keys()


@pytest.mark.parametrize("state", ["SITUATION", "SERVICES", "EVENT_DATE"])
def test_legal_domain_service_and_date_select_applicable_sources(state):
    current = question(state, situationAreas=["TREATMENT"])
    result = contracts.material_question(current)
    assert result.reason_code == "SOURCE_APPLICABILITY"
    assert result.question.question_sha256 == current.question_sha256


@pytest.mark.parametrize("state", ["EVENT", "CHRONOLOGY", "CLINIC_ACTIONS"])
def test_explicit_context_questions_select_actions_without_making_them_mandatory(state):
    current = question(state)
    result = contracts.material_question(current)
    assert result.reason_code == "ACTION_SELECTION"
    assert result.question == current


def test_extraction_slots_are_not_promoted_into_questions_to_ask():
    current = contracts.AnswerQuestion.model_validate({
        "schemaVersion": "intake-answer-question.v1", "wizardState": "ANALYSIS_CLARIFICATION",
        "targetField": "analysisClarificationAnswers", "scopeBasis": "PROPOSED_SERVICES",
        "serviceScopeSha256": "a" * 64,
        "clarificationRequest": {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0},
    })
    with pytest.raises(ValueError):
        contracts.material_question(current)


def test_forged_question_is_revalidated_without_authorizing_unknown_state():
    current = question("EVENT_DATE")
    current.__dict__["wizard_state"] = "APPROVE_ALL_NORMS"
    with pytest.raises(ValueError):
        contracts.material_question(current)
