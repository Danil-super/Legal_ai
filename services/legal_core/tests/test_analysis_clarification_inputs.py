"""Synthetic, user-statement-only clarification input boundary tests."""

import pytest
from pydantic import ValidationError

from legal_core.api_contracts import FactInput, TelegramIntakeDraftUpdateRequest


def _answers(value: object = "PAID") -> dict:
    return {"answers": [{
        "questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": value,
    }]}


def _fact(value: dict, **overrides: object) -> dict:
    return {"factKey": "ANALYSIS_CLARIFICATION_ANSWERS", "valueType": "JSON",
            "value": value, "sourceType": "USER_STATEMENT", **overrides}


def _draft(answers: dict) -> dict:
    return {"expectedRevision": 1, "wizardState": "SUMMARY", "draftData": {
        "intakeVersion": 2, "analysisClarificationAnswers": answers,
    }}


@pytest.mark.parametrize("value", ["PAID", "PUBLICLY_COVERED", "OTHER", "UNKNOWN"])
def test_confirmed_answers_roundtrip_at_fact_and_draft_boundary(value: str) -> None:
    answers = _answers(value)
    assert FactInput.model_validate(_fact(answers)).value == answers
    assert TelegramIntakeDraftUpdateRequest.model_validate(_draft(answers)).draft_data[
        "analysisClarificationAnswers"
    ] == answers


def test_partial_draft_and_omitted_answers_are_not_invented() -> None:
    assert FactInput.model_validate(_fact({"answers": []})).value == {"answers": []}
    draft = _draft({"answers": []})
    draft["draftData"].pop("analysisClarificationAnswers")
    validated = TelegramIntakeDraftUpdateRequest.model_validate(draft)
    assert "analysisClarificationAnswers" not in validated.draft_data


@pytest.mark.parametrize("answers", [
    _answers(False), _answers("SYNTHETIC_CANARY raw text"),
    {"answers": [{"questionId": "RESPONSE_PURPOSE", "value": "UNKNOWN", "clinicId": "foreign"}]},
    {"answers": [{"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 5,
                  "value": {"date": None, "precision": "UNKNOWN"}}]},
    {"answers": _answers()["answers"] * 2},
    {"answers": [], "rawDocument": "synthetic"},
    {"answers": [{"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
                  "value": {"date": "2026-02-30", "precision": "EXACT"}}]},
])
def test_malformed_answers_are_rejected_at_both_boundaries(answers: dict) -> None:
    with pytest.raises(ValidationError):
        FactInput.model_validate(_fact(answers))
    with pytest.raises(ValidationError):
        TelegramIntakeDraftUpdateRequest.model_validate(_draft(answers))


def test_new_answer_fact_requires_json_and_user_statement_provenance() -> None:
    for overrides in ({"valueType": "TEXT"}, {"sourceType": "MODEL"}):
        with pytest.raises(ValidationError):
            FactInput.model_validate(_fact(_answers(), **overrides))


def test_clarification_answers_cannot_be_added_to_legacy_draft() -> None:
    payload = _draft(_answers())
    payload["wizardState"] = "CONFIRM"
    payload["draftData"].pop("intakeVersion")
    with pytest.raises(ValidationError):
        TelegramIntakeDraftUpdateRequest.model_validate(payload)
