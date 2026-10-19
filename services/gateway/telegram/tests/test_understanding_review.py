# ruff: noqa: RUF001
"""Every proposed insertion must be visible before explicit acceptance."""

from legal_core.intake_preparation_contracts import DraftSuggestions
from telegram_gateway.understanding_review import proposal_blocks


def test_all_nine_proposal_fields_and_service_associations_are_visible():
    fields = {
        "incomingKind": "COMPLAINT", "situationAreas": ["TREATMENT"],
        "affectedServices": ["коронка", "пломба"],
        "eventDate": {"date": None, "precision": "UNKNOWN"},
        "conflictStage": "ONGOING", "clinicActions": ["OFFERED_CORRECTION"],
        "healthSignals": ["UNKNOWN"],
        "safetyScreening": {"schemaVersion": "factual-safety-intake.v1",
                            "moneyRequested": "NO", "amount": "NOT_REQUESTED"},
        "analysisClarificationAnswers": {"answers": [
            {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 1, "value": "UNKNOWN"},
            {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
             "value": {"date": "2026-01-02", "precision": "APPROXIMATE"}},
        ]},
    }
    proposal = DraftSuggestions.model_validate({
        **fields, "sourceSpans": {key: [{"start": 0, "end": 4}] for key in fields},
    })
    text = "\n".join(proposal_blocks({"intakeVersion": 2}, proposal))
    for expected in ("обращение с недовольством", "лечение", "коронка", "пломба",
                     "неизвестна", "продолжается", "коррекцию", "недостаточно",
                     "Денежная просьба: нет", "не запрошена", "Оплата услуги «пломба»",
                     "Дата услуги «коронка»: примерно 2026-01-02"):
        assert expected in text
    assert "Hermes" not in text and "sourceSpans" not in text


def test_proposed_services_remain_visible_even_without_proposed_area():
    proposal = DraftSuggestions.model_validate({
        "affectedServices": ["коронка"],
        "sourceSpans": {"affectedServices": [{"start": 0, "end": 4}]},
    })
    assert "коронка" in "\n".join(proposal_blocks({"intakeVersion": 2}, proposal))
