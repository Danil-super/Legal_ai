"""Unknown facts need a concrete question, not a frozen unrecoverable report."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from telegram.ext import ApplicationHandlerStop
from telegram_gateway import bot
from telegram_gateway.analysis_jobs_runtime import JobSnapshot, job_message
from telegram_gateway.analysis_runtime import analysis_error_message
from test_factual_safety_gateway import runtime_context
from test_guided_case_intake_v2 import _base_data


@pytest.mark.parametrize("field", ["representativeContact", "authorityReferralMentioned"])
def test_unknown_safety_fact_is_reasked_before_freezing_case(field):
    screening = {"schemaVersion": SCREENING_VERSION,
                 **dict.fromkeys(SCREENING_FIELDS, "NO"), "amount": "NOT_REQUESTED"}
    screening[field] = "UNKNOWN"
    data = _base_data() | {"safetyScreening": screening}
    update, context, _client, message = runtime_context(data, "case:v2:summary:confirm")
    result = asyncio.run(bot.confirm_v2_summary(update, context))
    assert result == bot.WizardState.SAFETY
    assert field not in data["safetyScreening"]
    assert all(data["safetyScreening"][f] == "NO" for f in SCREENING_FIELDS if f != field)
    assert any("уточн" in text.lower() for text in message.text_replies)


def test_stored_unknown_error_explains_the_actual_question():
    text = analysis_error_message("FACTUAL_SAFETY_REPRESENTATIVECONTACT_UNKNOWN")
    assert "представитель" in text.lower()
    assert "не удалось" not in text.lower()


def test_unknown_fact_job_offers_clarification_instead_of_repeating_same_analysis():
    case = UUID("00000000-0000-4000-8000-000000000099")
    job = JobSnapshot(UUID("00000000-0000-4000-8000-000000000098"), case, "FAILED",
                      "FACTUAL_SAFETY_REPRESENTATIVECONTACT_UNKNOWN", None)
    _text, keyboard = job_message(job)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert f"case:clarify:{case}" in callbacks
    assert f"case:analyze:{case}" not in callbacks


def test_clarification_retry_and_restart_use_same_intent_and_resume_the_new_draft():
    from telegram_gateway.clarification_runtime import create_clarification

    case = UUID("00000000-0000-4000-8000-000000000091")
    draft = UUID("00000000-0000-4000-8000-000000000092")
    client = SimpleNamespace(
        clarification_preview=AsyncMock(return_value={
            "caseId": str(case), "expectedFactSnapshotSha256": "a" * 64,
        }),
        create_clarification_draft=AsyncMock(return_value={
            "id": str(draft), "draftData": {"correctionSource": {
                "caseId": str(case), "factSnapshotSha256": "a" * 64,
            }},
        }),
    )
    message = SimpleNamespace(message_id=77, reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=7000000001),
                             effective_message=message, callback_query=SimpleNamespace(
                                 data=f"case:clarify:{case}", answer=AsyncMock(), message=message))
    for _ in range(2):
        context = SimpleNamespace(user_data={}, bot_data={bot.LEGAL_CORE_CLIENT_KEY: client})
        with pytest.raises(ApplicationHandlerStop):
            asyncio.run(create_clarification(update, context))
    first, second = client.create_clarification_draft.await_args_list
    assert first.kwargs["key"] == second.kwargs["key"]
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"case:draft:{draft}"
    assert markup.inline_keyboard[-1][0].callback_data == "menu"
