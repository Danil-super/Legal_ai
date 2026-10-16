"""Synthetic factual answers: no patient documents or live model calls."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from legal_core.intake_answer_contracts import (
    AnswerInterpretation,
    AnswerQuestion,
    derive_answer_question,
)
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION


@pytest.mark.parametrize(("state", "target"), [
    ("INCOMING", "incomingKind"), ("SITUATION", "situationAreas"),
    ("SERVICES", "affectedServices"), ("EVENT", "eventSummary"),
    ("EVENT_DATE", "eventDate"), ("CHRONOLOGY", "conflictStage"),
    ("CLINIC_ACTIONS", "clinicActions"), ("HEALTH", "healthSignals"),
])
def test_question_comes_from_persisted_current_step(state, target):
    data = {"intakeVersion": 2, "situationAreas": ["TREATMENT"],
            "affectedServices": ["Restoration"]}
    question = derive_answer_question(data, state)
    assert question is not None and question.target_field == target
    assert question.wizard_state == state
    canonical = json.dumps(question.model_dump(mode="json", by_alias=True),
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert question.question_sha256 == hashlib.sha256(canonical.encode()).hexdigest()


@pytest.mark.parametrize("state", ["MATERIALS", "SUMMARY", "V2_CONFIRM", "INCIDENT", "NOPE"])
def test_nonfactual_steps_cannot_be_interpreted(state):
    assert derive_answer_question({"intakeVersion": 2}, state) is None


def test_legacy_draft_and_non_treatment_services_are_rejected():
    with pytest.raises(ValueError):
        derive_answer_question({}, "EVENT")
    with pytest.raises(ValueError):
        derive_answer_question({"intakeVersion": 2}, "SERVICES")


def test_screening_selects_first_absent_not_explicit_unknown():
    data = {"intakeVersion": 2, "safetyScreening": {
        "schemaVersion": SCREENING_VERSION, SCREENING_FIELDS[0]: "UNKNOWN",
    }}
    question = derive_answer_question(data, "SAFETY")
    assert question.screening_field == SCREENING_FIELDS[1]
    data["safetyScreening"].update({key: "NO" for key in SCREENING_FIELDS})
    data["safetyScreening"]["moneyRequested"] = "YES"
    assert derive_answer_question(data, "SAFETY").screening_field == "amount"
    data["safetyScreening"]["amount"] = "UNKNOWN"
    assert derive_answer_question(data, "SAFETY") is None


def test_clarification_skips_attempts_and_binds_current_service_order():
    data = {"intakeVersion": 2, "situationAreas": ["TREATMENT"],
            "affectedServices": ["Restoration", "Extraction"],
            "analysisClarificationRequests": [
                {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
                {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 1},
            ], "analysisClarificationAnswers": {"answers": [
                {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "UNKNOWN"},
            ]}}
    question = derive_answer_question(data, "ANALYSIS_CLARIFICATION")
    assert question.clarification_request.question_id == "SERVICE_PROVIDED_DATE"
    assert question.clarification_request.service_slot == 1
    previous = question.question_sha256
    data["affectedServices"].reverse()
    assert derive_answer_question(data, "ANALYSIS_CLARIFICATION").question_sha256 != previous


@pytest.mark.parametrize("changes", [
    {"targetField": "eventDate"}, {"screeningField": "moneyRequested"},
    {"clarificationRequest": {"questionId": "RESPONSE_PURPOSE"}},
    {"serviceScopeSha256": "a" * 64}, {"extra": "forbidden"},
])
def test_question_rejects_cross_step_metadata(changes):
    with pytest.raises(ValidationError):
        AnswerQuestion.model_validate({"schemaVersion": "intake-answer-question.v1",
                                       "wizardState": "EVENT", "targetField": "eventSummary",
                                       **changes})


@pytest.mark.parametrize("payload", [
    {"status": "PROPOSED", "value": "UNKNOWN", "sourceSpans": []},
    {"status": "PROPOSED", "value": None, "sourceSpans": [{"start": 0, "end": 1}]},
    {"status": "PROPOSED", "value": "UNKNOWN", "sourceSpans": [{"start": 0, "end": 1}],
     "reasonCode": "AMBIGUOUS"},
    {"status": "UNRESOLVED", "value": "UNKNOWN", "sourceSpans": [], "reasonCode": "AMBIGUOUS"},
    {"status": "UNRESOLVED", "value": None, "sourceSpans": []},
])
def test_interpretation_does_not_confuse_unresolved_and_proposed(payload):
    with pytest.raises(ValidationError):
        AnswerInterpretation.model_validate(payload)


def test_answer_offsets_apply_only_to_actual_answer_not_draft_narrative():
    proposal = AnswerInterpretation.model_validate({
        "status": "PROPOSED", "value": "UNKNOWN", "sourceSpans": [{"start": 0, "end": 1}],
    })
    proposal.validate_source("?")
    with pytest.raises(ValueError):
        proposal.validate_source(" ")
    with pytest.raises(ValueError):
        proposal.validate_source("")
    with pytest.raises(ValueError):
        proposal.model_copy(update={"source_spans": [{"start": 0, "end": 2}]}).validate_source("?")
