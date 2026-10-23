"""Deletion is exact, actor/chat/draft/revision-bound and never a history sweep."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram_gateway.intake_cleanup import (
    KEY,
    Step,
    capture,
    delete_saved,
    finish,
    finish_waiting,
    recover_saved,
    remember_saved,
)

ACTOR = 777
CHAT = 777
DRAFT = "00000000-0000-0000-0000-000000000123"


def update(*, actor=ACTOR, chat=CHAT, text="Synthetic answer", callback=False):
    user = User(actor, "Synthetic", False)
    message = Message(12, datetime.now(UTC), Chat(chat, "private"), from_user=user, text=text)
    if callback:
        query = CallbackQuery("synthetic", user, "test", message=message, data="synthetic")
        return Update(1, callback_query=query)
    return Update(1, message=message)


@pytest.mark.parametrize("mismatch", [
    "actor", "chat", "draft", "same_revision", "old_revision", "boolean_revision",
    "missing_prompt", "boolean_prompt",
])
def test_delete_saved_refuses_unproven_scope_or_progress(mismatch):
    step = Step(ACTOR, CHAT, DRAFT, 4, (10, 12))
    event = update()
    saved = {"id": DRAFT, "revision": 5}
    current_prompt = 15
    if mismatch == "actor":
        event = update(actor=999)
    elif mismatch == "chat":
        event = update(chat=999)
    elif mismatch == "draft":
        saved["id"] = str(uuid4())
    elif mismatch == "same_revision":
        saved["revision"] = 4
    elif mismatch == "old_revision":
        saved["revision"] = 3
    elif mismatch == "boolean_revision":
        saved["revision"] = True
    elif mismatch == "missing_prompt":
        current_prompt = None
    else:
        current_prompt = True
    context = SimpleNamespace(bot=SimpleNamespace(delete_message=AsyncMock()))

    asyncio.run(delete_saved(event, context, step, saved, current_prompt))

    context.bot.delete_message.assert_not_awaited()


def test_current_prompt_never_deleted_even_when_in_previous_message_set():
    step = Step(ACTOR, CHAT, DRAFT, 4, (10, 12, 15))
    context = SimpleNamespace(bot=SimpleNamespace(delete_message=AsyncMock()))

    asyncio.run(delete_saved(update(), context, step, {"id": DRAFT, "revision": 5}, 15))

    assert [call.kwargs for call in context.bot.delete_message.await_args_list] == [
        {"chat_id": CHAT, "message_id": 10}, {"chat_id": CHAT, "message_id": 12},
    ]


@pytest.mark.parametrize("state", ["EVENT", "SUMMARY", "CONFIRM", "V2_CONFIRM"])
def test_original_narrative_and_summary_text_are_never_captured_as_short_answer(state):
    step = capture(update(), DRAFT, 4, state, 10)

    assert step is not None and 12 not in step.messages
    if state != "EVENT":
        assert not step.messages


@pytest.mark.parametrize("text", ["/menu", "x" * 501])
def test_command_or_long_text_is_not_captured_as_disposable_answer(text):
    step = capture(update(text=text), DRAFT, 4, "SERVICES", 10)

    assert step is not None and step.messages == (10,)


def test_internal_callback_anchor_never_becomes_disposable_user_answer():
    step = capture(update(callback=True), DRAFT, 4, "SERVICES", 10)

    assert step is not None and step.messages == (10,)


def test_stale_operation_cannot_pop_or_delete_current_operation_messages():
    current, stale = uuid4(), uuid4()
    step = Step(ACTOR, CHAT, DRAFT, 4, (10, 12))
    context = SimpleNamespace(bot=SimpleNamespace(delete_message=AsyncMock()),
                              user_data={KEY: (current, step)})

    asyncio.run(finish(update(), context, stale, {"id": DRAFT, "revision": 5},
                       {"_v2_prompt_message_id": 15}))

    context.bot.delete_message.assert_not_awaited()
    assert context.user_data[KEY] == (current, step)


def test_finishing_current_operation_clears_only_its_ephemeral_metadata():
    current = uuid4()
    step = Step(ACTOR, CHAT, DRAFT, 4, (10, 12))
    context = SimpleNamespace(bot=SimpleNamespace(delete_message=AsyncMock()),
                              user_data={KEY: (current, step), "unrelated": "preserve"})

    asyncio.run(finish(update(), context, current, {"id": DRAFT, "revision": 5},
                       {"_v2_prompt_message_id": 15}))

    assert context.user_data == {"unrelated": "preserve"}
    assert context.bot.delete_message.await_count == 2


@pytest.mark.parametrize("mismatch", [
    "no_proof", "changed_facts", "actor", "chat", "draft", "older_revision", "missing_prompt",
])
def test_recovery_never_infers_safe_deletion_from_revision_movement_or_foreign_context(mismatch):
    operation = uuid4()
    context = SimpleNamespace(bot=SimpleNamespace(delete_message=AsyncMock()),
                              user_data={KEY: (operation, Step(ACTOR, CHAT, DRAFT, 4, (10, 12)))})
    staged = {"id": DRAFT, "revision": 5, "draftData": {"incomingKind": "COMPLAINT"}}
    if mismatch != "no_proof":
        remember_saved(context, operation, staged)
    recovered = {**staged, "revision": 7}
    event, wizard = update(), {"_v2_prompt_message_id": 15}
    if mismatch == "changed_facts":
        recovered["draftData"] = {"incomingKind": "FORMAL_DOCUMENT"}
    elif mismatch == "actor":
        event = update(actor=999)
    elif mismatch == "chat":
        event = update(chat=999)
    elif mismatch == "draft":
        recovered["id"] = str(uuid4())
    elif mismatch == "older_revision":
        recovered["revision"] = 4
    elif mismatch == "missing_prompt":
        wizard = {}

    asyncio.run(recover_saved(event, context, recovered, wizard))

    context.bot.delete_message.assert_not_awaited()


def test_stale_operation_cannot_install_staging_proof_for_a_new_answer():
    current, stale = uuid4(), uuid4()
    step = Step(ACTOR, CHAT, DRAFT, 4, (10, 12))
    context = SimpleNamespace(bot=SimpleNamespace(delete_message=AsyncMock()),
                              user_data={KEY: (current, step)})
    recovered = {"id": DRAFT, "revision": 7, "draftData": {"incomingKind": "COMPLAINT"}}

    remember_saved(context, stale, recovered)
    asyncio.run(recover_saved(update(), context, recovered, {"_v2_prompt_message_id": 15}))

    assert context.user_data[KEY] == (current, step)
    context.bot.delete_message.assert_not_awaited()


def test_recovery_with_exact_saved_proof_can_clean_after_only_navigation_revision_changes():
    operation = uuid4()
    context = SimpleNamespace(bot=SimpleNamespace(delete_message=AsyncMock()),
                              user_data={KEY: (operation, Step(ACTOR, CHAT, DRAFT, 4, (10, 12)))})
    staged = {"id": DRAFT, "revision": 5, "draftData": {"incomingKind": "COMPLAINT"}}
    remember_saved(context, operation, staged)

    asyncio.run(recover_saved(update(), context, {**staged, "revision": 7},
                              {"_v2_prompt_message_id": 15}))

    assert KEY not in context.user_data
    assert context.bot.delete_message.await_count == 2


def test_stale_correction_completion_cannot_delete_a_new_operations_waiting_message():
    current, stale = uuid4(), uuid4()
    step = Step(ACTOR, CHAT, DRAFT, 5, (10, 12, 14), waits=(14,))
    context = SimpleNamespace(bot=SimpleNamespace(delete_message=AsyncMock()),
                              user_data={KEY: (current, step)})
    new_prompt = SimpleNamespace(chat_id=CHAT, message_id=15)

    asyncio.run(finish_waiting(update(), context, stale, new_prompt))

    assert context.user_data[KEY] == (current, step)
    context.bot.delete_message.assert_not_awaited()


def test_correction_delivery_deletes_only_its_wait_not_unconfirmed_user_answer_or_question():
    operation = uuid4()
    step = Step(ACTOR, CHAT, DRAFT, 4, (10, 12, 14), waits=(14,))
    context = SimpleNamespace(bot=SimpleNamespace(delete_message=AsyncMock()),
                              user_data={KEY: (operation, step)})

    asyncio.run(finish_waiting(update(), context, operation,
                               SimpleNamespace(chat_id=CHAT, message_id=15)))

    assert [call.kwargs for call in context.bot.delete_message.await_args_list] == [
        {"chat_id": CHAT, "message_id": 14},
    ]
    assert context.user_data[KEY] == (operation, step)
