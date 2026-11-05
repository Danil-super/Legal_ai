"""Recovery keys bind the original message; no message contents enter metadata."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from telegram.ext import ApplicationHandlerStop
from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.escalation_message_runtime import (
    PENDING_KEY,
    message_operation,
    pending_operation,
    retry_message,
    submit_message,
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


def test_empty_context_and_new_message_id_is_a_new_intent_even_for_identical_text():
    escalation = uuid4()
    first_context = SimpleNamespace(user_data={})
    first = pending_operation(update(10), first_context, escalation, "Synthetic text.", False)
    # Gateway restart loses the old pending binding; text alone is not an operation ID.
    restarted = pending_operation(update(11), SimpleNamespace(user_data={}), escalation,
                                  "Synthetic text.", False)
    assert first != restarted
    assert restarted[0] == message_operation(777, escalation, False, 11)


def test_uncertain_response_directs_to_recovery_button_without_a_manual_resend_guarantee():
    async def scenario():
        escalation = uuid4()
        core = SimpleNamespace(post_escalation_discussion_message=AsyncMock(
            side_effect=LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "synthetic outage")))
        event, context = update(), SimpleNamespace(user_data={},
                                                  bot_data={"legal_core_client": core})
        with pytest.raises(ApplicationHandlerStop):
            await submit_message(event, context, escalation=escalation, body="Synthetic text.",
                                 resolving=False, operation=uuid4(), source=10)
        call = event.effective_message.reply_text.call_args
        assert "Проверить отправку" in call.args[0]
        assert "повторите тот же текст" not in call.args[0] and "дубля" not in call.args[0]
        assert call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.startswith(
            "esc:retrymsg:")

    asyncio.run(scenario())


@pytest.mark.parametrize("intake", [False, True])
@pytest.mark.parametrize("mode", ["m", "c"])
def test_successful_recovery_preserves_input_mode_and_offers_explicit_case_navigation(intake, mode):
    async def scenario():
        escalation = uuid4()
        original = SimpleNamespace(message_id=10, text="Synthetic text.",
            from_user=SimpleNamespace(id=777), chat=SimpleNamespace(type="private"))
        event = update()
        event.callback_query = SimpleNamespace(data=f"esc:retrymsg:{escalation.hex}:{mode}:10",
            answer=AsyncMock(), message=SimpleNamespace(reply_to_message=original))
        core = SimpleNamespace(post_escalation_discussion_message=AsyncMock(return_value={}),
                               resolve_escalation=AsyncMock(return_value={}))
        data = {gateway_bot.WIZARD_DATA_KEY: {"draft": "synthetic"}} if intake else {}
        before = deepcopy(data)
        context = SimpleNamespace(user_data=data, bot_data={"legal_core_client": core})
        with pytest.raises(ApplicationHandlerStop):
            await retry_message(event, context)
        call = event.effective_message.reply_text.call_args
        assert "Можно написать следующий" not in call.args[0]
        assert "проверена" in call.args[0]
        buttons = [button for row in call.kwargs["reply_markup"].inline_keyboard for button in row]
        assert any(b.callback_data == f"case:escalation:{escalation}" for b in buttons)
        assert data == before  # No new discussion mode; no intake state is stolen.

    asyncio.run(scenario())


def test_direct_post_in_the_active_discussion_still_accepts_the_next_text_message():
    from telegram_gateway.analysis_runtime import post_escalation_discussion_message

    async def scenario():
        escalation = uuid4()
        event = update()
        event.effective_message.text = "Synthetic text."
        core = SimpleNamespace(post_escalation_discussion_message=AsyncMock(return_value={}))
        context = SimpleNamespace(user_data={"escalation_discussion_id": str(escalation)},
                                  bot_data={"legal_core_client": core})
        with pytest.raises(ApplicationHandlerStop):
            await post_escalation_discussion_message(event, context)
        assert "Можно написать следующий" in event.effective_message.reply_text.call_args.args[0]
        assert context.user_data["escalation_discussion_id"] == str(escalation)
        core.post_escalation_discussion_message.assert_awaited_once()

    asyncio.run(scenario())


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
