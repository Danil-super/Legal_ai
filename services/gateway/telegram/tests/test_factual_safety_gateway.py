"""Authored factual replies, not medical records or legal qualification."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from legal_core.api_contracts import TelegramIntakeDraftUpdateRequest
from legal_core.contracts import FactKey
from legal_core.factual_safety_intake import FactualSafetyScreening
from legal_core.factual_safety_risk import SCREENING_SIGNALS
from pydantic import ValidationError
from telegram_gateway import bot
from telegram_gateway.case_wizard import facts_from_v2_data
from telegram_gateway.factual_safety_intake import (
    answer_screening,
    known_urgent_report,
    previous_screening_question,
    screening_question,
    screening_summary,
)
from telegram_gateway.guided_case_intake_v2 import V2_SAFETY, next_missing_state
from test_case_wizard import FakeMessage, FakeQuery, FakeReportPipeline
from test_guided_case_intake_v2 import _base_data


def completed_data(**overrides):
    data = _base_data()
    for field in SCREENING_SIGNALS:
        answer_screening(data, field, "NO")
    data["safetyScreening"].update(overrides)
    return data


def test_seven_independent_answers_do_not_manufacture_legacy_negatives():
    data = _base_data()
    assert next_missing_state(data) == V2_SAFETY
    for field in SCREENING_SIGNALS:
        assert screening_question(data)[0] == field
        answer_screening(data, field, "NO")
    assert screening_question(data) is None
    parsed = FactualSafetyScreening.model_validate(data["safetyScreening"])
    assert parsed.amount == "NOT_REQUESTED"
    facts = facts_from_v2_data(data)
    screening = next(item for item in facts if item["factKey"] == "FACTUAL_SAFETY_SCREENING")
    assert screening["valueType"] == "JSON"
    assert screening["value"] == data["safetyScreening"]
    assert all(
        item["factKey"] not in {FactKey.HARM_CLAIMED, FactKey.FORMAL_CLAIM} for item in facts
    )
    assert data["healthSignals"] == ["NO_KNOWN_INFORMATION"]


def test_unknown_answers_stay_unknown_and_requested_money_uses_exact_kopecks():
    data = completed_data()
    answer_screening(data, "moneyRequested", "YES")
    assert screening_question(data)[0] == "amount"
    answer_screening(data, "amount", "49 999,99")
    assert data["safetyScreening"]["amount"] == {"amountKopecks": 4_999_999, "currency": "RUB"}
    answer_screening(data, "representativeContact", "UNKNOWN")
    assert data["safetyScreening"]["representativeContact"] == "UNKNOWN"
    assert "неизвестно" in "\n".join(screening_summary(data))
    answer_screening(data, "moneyRequested", "UNKNOWN")
    assert data["safetyScreening"]["amount"] == "UNKNOWN"


def test_back_reopens_last_factual_question_without_losing_previous_answers():
    data = completed_data()
    assert previous_screening_question(data) is True
    assert screening_question(data)[0] == "moneyRequested"
    assert data["safetyScreening"]["representativeContact"] == "NO"
    for _ in range(6):
        assert previous_screening_question(data) is True
    assert previous_screening_question(data) is False


def test_partial_screening_survives_durable_draft_contract():
    data = _base_data()
    answer_screening(data, "healthDeteriorationReported", "UNKNOWN")
    request = TelegramIntakeDraftUpdateRequest(
        expectedRevision=2,
        wizardState="SAFETY",
        draftData=data,
    )
    assert request.draft_data["safetyScreening"]["healthDeteriorationReported"] == "UNKNOWN"


@pytest.mark.parametrize(
    "invalid",
    [
        True,
        {"extra": "NO"},
        {
            "schemaVersion": "factual-safety-intake.v1",
            "representativeContact": False,
        },
        {
            "schemaVersion": "factual-safety-intake.v1",
            "representativeContact": ["NO"],
        },
    ],
)
def test_invalid_partial_screening_is_rejected_at_durable_boundary(invalid):
    data = _base_data() | {"safetyScreening": invalid}
    with pytest.raises(ValidationError):
        TelegramIntakeDraftUpdateRequest(expectedRevision=1, wizardState="SAFETY", draftData=data)


def runtime_context(data, callback):
    draft_id = str(uuid4())
    data.update(workflow_id=draft_id, draft_id=draft_id, draft_revision=2, draft_state="SAFETY")
    pipeline = FakeReportPipeline()
    message = FakeMessage()
    update = SimpleNamespace(
        callback_query=FakeQuery(callback),
        effective_message=message,
        effective_user=SimpleNamespace(id=7_000_000_001),
    )
    context = SimpleNamespace(
        bot_data={bot.LEGAL_CORE_CLIENT_KEY: pipeline}, user_data={bot.WIZARD_DATA_KEY: data}
    )
    return update, context, pipeline, message


def test_actual_handler_persists_each_answer_and_ignores_stale_other_question():
    update, context, pipeline, message = runtime_context(
        _base_data(), "case:safety:representativeContact:NO"
    )
    assert asyncio.run(bot.choose_factual_safety(update, context)) == bot.WizardState.SAFETY
    assert "safetyScreening" not in context.user_data[bot.WIZARD_DATA_KEY]
    for field in SCREENING_SIGNALS:
        update.callback_query = FakeQuery(f"case:safety:{field}:NO")
        result = asyncio.run(bot._persist_transition(bot.choose_factual_safety, update, context))
    assert result == bot.WizardState.SUMMARY
    assert pipeline.steps == ["save"] * 7
    assert any("Письменные требования: нет" in text for text in message.text_replies)
    assert all(
        "case:v2:back" in {b.callback_data for row in markup.inline_keyboard for b in row}
        for markup in message.text_reply_markups
        if markup is not None
    )


def test_stale_summary_and_submit_cannot_bypass_new_independent_questions():
    update, context, pipeline, _ = runtime_context(_base_data(), "case:v2:summary:confirm")
    assert asyncio.run(bot.confirm_v2_summary(update, context)) == bot.WizardState.SAFETY
    draft = context.user_data[bot.WIZARD_DATA_KEY]
    update.callback_query = FakeQuery(f"case:confirm:{draft['workflow_id']}")
    assert asyncio.run(bot.confirm_case(update, context)) == bot.WizardState.SAFETY
    assert pipeline.steps == []


def test_back_from_summary_reopens_money_and_keeps_other_answers():
    update, context, _, _ = runtime_context(completed_data(), "case:v2:back")
    context.user_data[bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] = "SUMMARY"
    assert asyncio.run(bot.back_v2_draft(update, context)) == bot.WizardState.SAFETY
    assert screening_question(context.user_data[bot.WIZARD_DATA_KEY])[0] == "moneyRequested"


def test_urgent_known_facts_can_be_confirmed_without_answering_every_other_question():
    data = _base_data()
    data["healthSignals"] = ["HOSPITALIZATION"]
    assert known_urgent_report(data) is True
    update, context, _, message = runtime_context(data, "case:safety:known:confirm")
    assert asyncio.run(bot.choose_factual_safety(update, context)) == bot.WizardState.SUMMARY
    screening = data["safetyScreening"]
    assert all(screening[field] == "UNKNOWN" for field in SCREENING_SIGNALS)
    assert screening["amount"] == "UNKNOWN"
    assert "Госпитализация" not in "\n".join(message.text_replies)  # no inferred new answer
    assert data["healthSignals"] == ["HOSPITALIZATION"]


def test_unknown_or_plain_document_does_not_offer_urgent_shortcut():
    data = _base_data()
    data["incomingKind"] = "FORMAL_DOCUMENT"
    assert known_urgent_report(data) is False
    update, context, _, _ = runtime_context(data, "case:safety:known:confirm")
    assert asyncio.run(bot.choose_factual_safety(update, context)) == bot.WizardState.SAFETY
    assert "safetyScreening" not in data
