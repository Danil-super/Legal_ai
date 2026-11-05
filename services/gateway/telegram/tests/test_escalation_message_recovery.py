"""Recovery keys bind the original message; no message contents enter metadata."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from telegram.ext import ApplicationHandlerStop
from telegram_gateway.escalation_message_runtime import (
    PENDING_KEY,
    message_operation,
    pending_operation,
    retry_message,
)


def update(message_id=10):
    return SimpleNamespace(effective_user=SimpleNamespace(id=777),
        effective_message=SimpleNamespace(message_id=message_id, reply_text=AsyncMock()))


def test_operation_survives_an_update_retry_and_an_empty_gateway_context():
    escalation = uuid4()
    first = pending_operation(update(), SimpleNamespace(user_data={}), escalation,
                              "Synthetic text.", False)
    after_restart = pending_operation(update(), SimpleNamespace(user_data={}), escalation,
                                      "Synthetic text.", False)
    assert first == after_restart


def test_uncertain_same_text_reuses_intent_but_new_text_uses_its_new_message_id():
    context = SimpleNamespace(user_data={})
    escalation = uuid4()
    first = pending_operation(update(10), context, escalation, "Synthetic first text.", False)
    retry = pending_operation(update(11), context, escalation, "Synthetic first text.", False)
    assert first == retry
    second = pending_operation(update(12), context, escalation, "Synthetic second text.", False)
    assert second != first
    assert second[0] == message_operation(777, escalation, False, 12)
    assert "Synthetic" not in str(context.user_data[PENDING_KEY])


def test_successful_intent_does_not_absorb_a_later_intent_with_identical_text():
    context = SimpleNamespace(user_data={})
    escalation = uuid4()
    first = pending_operation(update(10), context, escalation, "Synthetic text.", False)
    context.user_data.pop(PENDING_KEY)
    second = pending_operation(update(11), context, escalation, "Synthetic text.", False)
    assert second != first
    assert message_operation(777, escalation, True, 10) != first[0]
    assert message_operation(778, escalation, False, 10) != first[0]


@pytest.mark.parametrize("change", ["missing", "sender", "group", "message-id"])
def test_recovery_button_cannot_replay_an_unverifiable_original_message(change):
    async def scenario():
        escalation = uuid4()
        original = SimpleNamespace(message_id=10, text="Synthetic text.",
            from_user=SimpleNamespace(id=778 if change == "sender" else 777),
            chat=SimpleNamespace(type="group" if change == "group" else "private"))
        if change == "message-id":
            original.message_id = 11
        event = update()
        event.callback_query = SimpleNamespace(data=f"esc:retrymsg:{escalation.hex}:m:10",
            answer=AsyncMock(), message=SimpleNamespace(
                reply_to_message=None if change == "missing" else original))
        context = SimpleNamespace(user_data={}, bot_data={})
        with pytest.raises(ApplicationHandlerStop):
            await retry_message(event, context)
        assert event.effective_message.reply_text.await_count == 1
        assert context.user_data == {}

    asyncio.run(scenario())
