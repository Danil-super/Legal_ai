"""One explicit summary click submits the case and starts recommendations."""

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from telegram.ext import ConversationHandler
from telegram_gateway import bot
from telegram_gateway.dialogue_staging import ENABLED
from test_case_wizard import FakeQuery
from test_initial_preparation_runtime import _context as _initial_context
from test_shared_preparation_form import with_unknown_clarification_attempts


def _context():
    result = _initial_context()
    data = result[1].user_data[bot.WIZARD_DATA_KEY]
    data.update(with_unknown_clarification_attempts(data))
    return result


@pytest.mark.parametrize("urgent", [False, True])
def test_single_final_summary_click_submits_once_and_starts_analysis_or_human_route(urgent):
    update, context, core, message, _ = _context()
    update.callback_query = FakeQuery("case:v2:summary:confirm")
    context.bot_data[ENABLED] = True
    receipt = core.submit_workflow.return_value
    case_id = UUID(receipt["case"]["id"])
    escalation = uuid4() if urgent else None
    receipt["case"]["earlyEscalationId"] = str(escalation) if escalation else None
    with patch(
        "telegram_gateway.dialogue_submission.enqueue_analysis", new_callable=AsyncMock,
    ) as enqueue:
        state = asyncio.run(bot.confirm_case(update, context))
    assert state == ConversationHandler.END
    core.submit_workflow.assert_awaited_once()
    if urgent:
        enqueue.assert_not_awaited()
        assert any("юрист" in text for text in message.text_replies)
    else:
        enqueue.assert_awaited_once_with(update, context, case_id)
    core.download_pdf.assert_not_awaited()
    assert not message.documents
    assert not any("Откройте обработку" in text for text in message.text_replies)


def test_one_click_summary_does_not_bypass_missing_facts():
    update, context, core, _message, _ = _context()
    update.callback_query = FakeQuery("case:v2:summary:confirm")
    context.bot_data[ENABLED] = True
    context.user_data[bot.WIZARD_DATA_KEY].pop("eventSummary")
    with patch(
        "telegram_gateway.dialogue_submission.enqueue_analysis", new_callable=AsyncMock,
    ) as enqueue:
        state = asyncio.run(bot.confirm_case(update, context))
    assert state == bot.WizardState.EVENT
    core.submit_workflow.assert_not_awaited()
    enqueue.assert_not_awaited()
