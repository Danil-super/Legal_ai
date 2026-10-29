"""Bounded status delivery through the same actor-serial PTB dispatcher as user input.

The watcher only reads operation status. It cannot confirm facts, rerun a model,
or mutate conversation/user data. A private refresh update rechecks authorization
and the selected draft before the ordinary handler renders or advances anything.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from telegram import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)
from telegram.ext import ContextTypes

from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.processing_activity import TypingPulse

POLL_INTERVAL = 1.0
MAX_POLLS = 90
MAX_WATCHERS = 64
MAX_READ_FAILURES = 3
_KEY = "intake_delivery_tasks"


class _ReadyQuery(CallbackQuery):
    """Internal refresh, not a Telegram click or an assertion of user consent."""

    async def answer(self, *args: Any, **kwargs: Any) -> bool:
        return True


async def recovery_notice(update: Update, draft_id: UUID) -> None:
    """Content-free recovery, not a claim that an in-flight operation stopped."""
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Результат разбора пока недоступен. Повторно откройте текущий "
            "черновик: бот проверит результат. Сохранённые ранее сведения не потеряны.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Продолжить текущий кейс",
                                      callback_data=f"case:draft:{draft_id}")],
                [InlineKeyboardButton("← Главное меню", callback_data="menu")],
            ]),
        )


def stop(update: Update, context: ContextTypes.DEFAULT_TYPE, selected_key: str) -> None:
    actor = update.effective_user.id if update.effective_user is not None else None
    tasks = context.application.bot_data.get(_KEY, {})
    entry = tasks.pop((actor, selected_key), None)
    if entry is not None and entry[1] is not asyncio.current_task():
        entry[1].cancel()


def watch(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, selected_key: str,
    operation: UUID, draft_id: UUID, client: Any, callback: str,
) -> bool:
    user, chat, message = update.effective_user, update.effective_chat, update.effective_message
    if user is None or chat is None or message is None or chat.type != "private":
        return False
    app, actor, chat_id, message_id = context.application, user.id, chat.id, message.message_id
    tasks = app.bot_data.setdefault(_KEY, {})
    key = (actor, selected_key)
    expected = {"draftId": str(draft_id), "operationId": str(operation)}
    previous = tasks.get(key)
    if previous is not None:
        if previous[0] == expected and not previous[1].done():
            return True
        previous[1].cancel()
        tasks.pop(key, None)
    if len(tasks) >= MAX_WATCHERS:
        return False  # Caller renders recovery inline; never grow without bound.

    def current() -> bool:
        return app.user_data.get(actor, {}).get(selected_key) == expected

    async def dispatch_result(*, unavailable: bool = False) -> None:
        person = User(actor, "Internal refresh", False)
        anchor = Message(message_id, datetime.now(UTC), Chat(chat_id, "private"),
                         from_user=person)
        anchor.set_bot(app.bot)
        query = _ReadyQuery(id="internal-intake-delivery", from_user=person,
                            chat_instance="internal", message=anchor, data=callback)
        query.set_bot(app.bot)
        event = Update(0, callback_query=query)
        event.set_bot(app.bot)

        async def dispatch() -> None:
            if current():
                if unavailable:
                    await recovery_notice(event, draft_id)
                else:
                    await app.process_update(event)

        # Recovery and results share the same actor fence as menu/cancel/edit.
        await app.update_processor.process_update(event, dispatch())

    async def deliver() -> None:
        try:
            pulse = TypingPulse()
            failures = 0
            for _ in range(MAX_POLLS):
                await asyncio.sleep(POLL_INTERVAL)
                if not current():
                    return
                try:
                    status = await asyncio.wait_for(client.get(operation), timeout=6)
                except (LegalCoreApiError, ValueError, TypeError, KeyError, TimeoutError):
                    failures += 1
                    if failures >= MAX_READ_FAILURES:
                        break
                    continue  # Retry this GET only, never restart the model.
                if not current():
                    return
                failures = 0
                if status.state == "PENDING":
                    await pulse.send(app.bot, actor)
                    continue
                await dispatch_result()
                return
            await dispatch_result(unavailable=True)
        finally:
            if tasks.get(key) == (expected, asyncio.current_task()):
                tasks.pop(key, None)

    pending = deliver()
    task = app.create_task(pending, name="intake-status-delivery")
    # PTB wraps the coroutine: cancellation before the wrapper's first step must
    # also close the inner coroutine (otherwise shutdown emits an unawaited warning).
    task.add_done_callback(lambda _finished: pending.close())
    tasks[key] = (expected, task)
    return True
