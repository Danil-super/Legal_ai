"""Synthetic reproduction of material questions discovered only after final consent."""

from copy import deepcopy
from datetime import date
from uuid import uuid4

from legal_core.analysis_clarifications import intake_clarification_requests
from legal_core.intake_answer_contracts import AnswerInterpretation, derive_answer_question
from legal_core.intake_factual_candidates import (
    allowed_factual_questions,
    apply_factual_answer,
    retained_answer_sources,
)
from telegram_gateway.guided_case_intake_v2 import (
    V2_ANALYSIS_CLARIFICATION,
    V2_SUMMARY,
    next_clarification_request,
    next_missing_state,
    previous_clarification_question,
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


def test_five_existing_services_have_one_complete_collection_plan_without_report_batch_overflow():
    data = complete_treatment_input()
    data["affectedServices"] = [f"Синтетическая услуга {slot}" for slot in range(5)]
    requests = intake_clarification_requests(data)
    assert len(requests) == 11
    assert len({identity(request) for request in requests}) == 11
    assert {request.service_slot for request in requests} == {None, 0, 1, 2, 3, 4}
    assert "analysisClarificationRequests" not in data


def test_other_topics_collect_purpose_without_inventing_services_or_requiring_treatment_details():
    data = complete_treatment_input()
    data["situationAreas"] = ["MEDICAL_RECORDS"]
    data.pop("affectedServices")
    assert [identity(item) for item in intake_clarification_requests(data)] == [
        ("RESPONSE_PURPOSE", None),
    ]


def test_explicit_back_can_edit_an_attempt_without_automatic_reasking():
    data = complete_treatment_input()
    data["analysisClarificationAnswers"] = {"answers": [
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "UNKNOWN"},
    ]}
    assert identity(next_clarification_request(data)) == ("SERVICE_PROVIDED_DATE", 0)
    assert previous_clarification_question(data)
    assert identity(next_clarification_request(data)) == ("SERVICE_PAYMENT_BASIS", 0)


def test_health_reply_reuses_material_facts_in_initial_narrative_without_another_round():
    data = complete_treatment_input()
    data.pop("healthSignals")
    data["eventSummary"] = (
        "Синтетический пример: установка коронки платная, оказана 01.10.2026. "
        "Нужно ответить на запрос."
    )
    original = deepcopy(data)
    answer = "Сведений по последствиям нет"
    question = derive_answer_question(data, "HEALTH")
    sources = retained_answer_sources(uuid4(), 3, data, answer)
    values = {
        ("SERVICE_PAYMENT_BASIS", 0): ("PAID", "платная"),
        ("SERVICE_PROVIDED_DATE", 0): (
            {"date": "2026-10-01", "precision": "EXACT"}, "01.10.2026",
        ),
        ("RESPONSE_PURPOSE", None): ("ANSWER_REQUEST", "Нужно ответить на запрос"),
    }
    additions = []
    for target in allowed_factual_questions(data, question):
        if target.clarification_request is None:
            continue
        value, quote = values[identity(target.clarification_request)]
        start = sources[0].text.index(quote)
        additions.append({
            "question": target.model_dump(mode="json", by_alias=True), "value": value,
            "anchors": [{"sourceId": str(sources[0].source_id),
                         "sourceSha256": sources[0].source_sha256,
                         "start": start, "end": start + len(quote)}],
        })
    proposal = AnswerInterpretation.model_validate({
        "status": "PROPOSED", "value": ["NO_KNOWN_INFORMATION"],
        "sourceSpans": [{"start": 0, "end": len(answer)}],
        "factualProposal": {"schemaVersion": "ai-first-factual-proposal.v1",
                            "candidates": additions},
    })
    result = apply_factual_answer(data, question, proposal, answer, sources,
                                 as_of_date=date(2026, 10, 28))
    assert next_missing_state(result) == V2_SUMMARY
    assert len(result["analysisClarificationAnswers"]["answers"]) == 3
    assert data == original
    assert "confirmed" not in result
