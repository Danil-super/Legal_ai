"""Manager-facing intake controls contain no no-op or implementation jargon."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram_gateway import bot


def _prompt(state):
    message = SimpleNamespace(reply_text=AsyncMock(
        return_value=SimpleNamespace(message_id=123),
    ))
    update = SimpleNamespace(effective_message=message)
    asyncio.run(bot._prompt_v2_draft(update, state, {}))
    return message.reply_text.call_args


def test_first_step_does_not_offer_back_to_the_same_step():
    call = _prompt(bot.WizardState.INCOMING)
    callbacks = [button.callback_data for row in call.kwargs["reply_markup"].inline_keyboard
                 for button in row]
    assert "case:v2:back" not in callbacks
    assert callbacks  # Choice and exit controls remain present.


def test_later_step_keeps_previous_step_navigation():
    call = _prompt(bot.WizardState.MATERIALS)
    callbacks = [button.callback_data for row in call.kwargs["reply_markup"].inline_keyboard
                 for button in row]
    assert "case:v2:back" in callbacks
    assert "case:v2:material:skip" in callbacks


def test_material_upload_explains_business_purpose_without_model_names():
    text = _prompt(bot.WizardState.MATERIALS).args[0]
    assert "Hermes" not in text
    assert "LLM" not in text
    assert "материалах кейса" in text
    assert "Удалите ФИО" in text
