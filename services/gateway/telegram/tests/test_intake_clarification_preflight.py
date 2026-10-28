"""Synthetic reproduction of material questions discovered only after final consent."""

from copy import deepcopy

from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_factual_candidates import allowed_factual_questions
from telegram_gateway.guided_case_intake_v2 import (
    V2_ANALYSIS_CLARIFICATION,
    V2_SUMMARY,
    next_clarification_request,
    next_missing_state,
)


def complete_treatment_input():
    return {
        "intakeVersion": 2,
        "incomingKind": "MESSAGE_OR_REQUEST",
        "incomingSourceStatus": "NOT_ATTACHED",
        "situationAreas": ["TREATMENT"],
        "affectedServices": ["Синтетическая установка коронки"],
        "eventSummary": "Синтетический пример: пациент просит объяснить результат лечения.",
        "eventDate": {"date": "2026-10-15", "precision": "EXACT"},
        "healthSignals": ["NO_KNOWN_INFORMATION"],
        "safetyScreening": {
            "schemaVersion": "factual-safety-intake.v1",
            "healthDeteriorationReported": "NO",
            "hospitalizationReported": "NO",
            "representativeContact": "NO",
            "writtenRequirementsReceived": "NO",
            "authorityOrCourtDocumentReceived": "NO",
            "authorityReferralMentioned": "NO",
            "moneyRequested": "NO",
            "amount": "NOT_REQUESTED",
        },
    }


def identity(request):
    return request.question_id, request.service_slot


def test_treatment_material_questions_are_collected_before_the_first_final_summary():
    data = complete_treatment_input()
    untouched = deepcopy(data)
    expected = {
        ("SERVICE_PAYMENT_BASIS", 0): "PAID",
        ("SERVICE_PROVIDED_DATE", 0): {"date": "2026-10-01", "precision": "EXACT"},
        ("RESPONSE_PURPOSE", None): "RECEIVE_AND_EXPLAIN",
    }
    seen = []
    while next_missing_state(data) == V2_ANALYSIS_CLARIFICATION:
        request = next_clarification_request(data)
        key = identity(request)
        assert key not in seen
        seen.append(key)
        data.setdefault("analysisClarificationAnswers", {"answers": []})["answers"].append({
            **request.model_dump(mode="json", by_alias=True), "value": expected[key],
        })
    assert set(seen) == set(expected)
    assert next_missing_state(data) == V2_SUMMARY
    assert untouched["eventDate"] == {"date": "2026-10-15", "precision": "EXACT"}


def test_remaining_material_questions_reuse_explicit_unknown_instead_of_reasking():
    data = complete_treatment_input()
    data["analysisClarificationAnswers"] = {"answers": [
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "UNKNOWN"},
        {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
         "value": {"date": None, "precision": "UNKNOWN"}},
    ]}
    assert next_missing_state(data) == V2_ANALYSIS_CLARIFICATION
    assert identity(next_clarification_request(data)) == ("RESPONSE_PURPOSE", None)
    data["analysisClarificationAnswers"]["answers"].append({
        "questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "value": "UNKNOWN",
    })
    assert next_missing_state(data) == V2_SUMMARY


def test_existing_narrative_can_fill_material_facts_before_any_analysis_requests_exist():
    data = complete_treatment_input()
    data.pop("healthSignals")
    current = derive_answer_question(data, "HEALTH")
    allowed = allowed_factual_questions(data, current)
    targets = {identity(item.clarification_request)
               for item in allowed if item.clarification_request is not None}
    assert targets == {
        ("SERVICE_PAYMENT_BASIS", 0), ("SERVICE_PROVIDED_DATE", 0),
        ("RESPONSE_PURPOSE", None),
    }


def test_known_urgent_report_reaches_summary_without_waiting_for_service_questions():
    data = complete_treatment_input()
    data["safetyScreening"]["hospitalizationReported"] = "YES"
    assert next_missing_state(data) == V2_SUMMARY
