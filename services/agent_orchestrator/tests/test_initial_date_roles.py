"""Synthetic provider boundary: preserve date roles through the actual Core merge."""

from datetime import date

from agent_orchestrator.intake_understanding import _SYSTEM
from test_intake_temporal_agent import _invoke

from legal_core.intake_preparation_merge import merge_preparation_suggestions
from telegram_gateway.guided_case_intake_v2 import V2_EVENT_DATE, next_missing_state

SOURCE = (
    "Учебный пример: установили коронку 10 сентября этого года, "
    "15 октября посетитель сообщил о спорной стоимости."
)


def test_prompt_defines_complaint_and_service_date_roles_with_one_bound_year():
    assert "eventDate is the patient's communication/complaint date" in _SYSTEM
    assert "SERVICE_PROVIDED_DATE is the date of that particular service" in _SYSTEM
    assert "same explicitly year-bound episode" in _SYSTEM


def test_two_anchored_dates_survive_model_validation_and_core_merge_without_reasking_event_date():
    fields = {
        "incomingKind": "COMPLAINT", "situationAreas": ["TREATMENT"],
        "affectedServices": ["коронку"],
        "eventDate": {"date": "2026-10-15", "precision": "EXACT"},
        "analysisClarificationAnswers": {"answers": [{
            "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
            "value": {"date": "2026-09-10", "precision": "EXACT"},
        }]},
    }
    proposal = _invoke(SOURCE, {**fields, "sourceSpans": {
        name: [{"start": 0, "end": len(SOURCE)}] for name in fields
    }}, [], as_of_date=date(2026, 10, 23))
    merged = merge_preparation_suggestions({
        "intakeVersion": 2, "eventSummary": SOURCE,
        "incomingSourceStatus": "NOT_ATTACHED", "caseMaterialsStatus": "NOT_ATTACHED",
    }, proposal, SOURCE)
    assert merged["eventDate"] == fields["eventDate"]
    assert merged["analysisClarificationAnswers"] == fields["analysisClarificationAnswers"]
    assert next_missing_state(merged) != V2_EVENT_DATE
