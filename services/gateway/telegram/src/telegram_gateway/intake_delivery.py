"""Bounded status delivery through the same actor-serial PTB dispatcher as user input.

The watcher only reads operation status. It cannot confirm facts, rerun a model,
or mutate conversation/user data. A private refresh update rechecks authorization
and the selected draft before the ordinary handler renders or advances anything.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import ContextTypes

from telegram_gateway.case_wizard import LegalCoreApiError

POLL_INTERVAL = 1.0
MAX_POLLS = 90
MAX_WATCHERS = 64
_KEY = "intake_delivery_tasks"


class _ReadyQuery(CallbackQuery):
    """Internal refresh, not a Telegram click or an assertion of user consent."""

    async def answer(self, *args: Any, **kwargs: Any) -> bool:
        return True


def stop(update: Update, context: ContextTypes.DEFAULT_TYPE, selected_key: str) -> None:
    actor = update.effective_user.id if update.effective_user is not None else None
    tasks = context.application.bot_data.get(_KEY, {})
    entry = tasks.pop((actor, selected_key), None)
    if entry is not None and entry[1] is not asyncio.current_task():
        entry[1].cancel()


def watch(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, selected_key: str,
    operation: UUID, draft_id: UUID, client: Any, callback: str,
) -> None:
    user, chat, message = update.effective_user, update.effective_chat, update.effective_message
    if user is None or chat is None or message is None or chat.type != "private":
        return
    app, actor, chat_id, message_id = context.application, user.id, chat.id, message.message_id
    tasks = app.bot_data.setdefault(_KEY, {})
    key = (actor, selected_key)
    expected = {"draftId": str(draft_id), "operationId": str(operation)}
    previous = tasks.get(key)
    if previous is not None:
        if previous[0] == expected and not previous[1].done():
            return
        previous[1].cancel()
        tasks.pop(key, None)
    if len(tasks) >= MAX_WATCHERS:
        return  # The visible recovery controls remain available; never grow without bound.

    def current() -> bool:
        return app.user_data.get(actor, {}).get(selected_key) == expected

    async def deliver() -> None:
        try:
            for _ in range(MAX_POLLS):
                await asyncio.sleep(POLL_INTERVAL)
                if not current():
                    return
                status = await asyncio.wait_for(client.get(operation), timeout=6)
                if not current():
                    return
                if status.state == "PENDING":
                    continue
                person = User(actor, "Internal refresh", False)
                anchor = Message(message_id, datetime.now(UTC), Chat(chat_id, "private"),
                                 from_user=person)
                anchor.set_bot(app.bot)
                query = _ReadyQuery(id="internal-intake-delivery", from_user=person,
                                    chat_instance="internal", message=anchor, data=callback)
                query.set_bot(app.bot)
                event = Update(0, callback_query=query)
                event.set_bot(app.bot)

                async def dispatch(event: Update = event) -> None:
                    if current():
                        await app.process_update(event)

                # A menu/cancel/edit already queued for this actor always fences the
                # refresh before it can touch the conversation. No private state APIs.
                await app.update_processor.process_update(event, dispatch())
                return
        except (LegalCoreApiError, ValueError, TypeError, KeyError, TimeoutError):
            return  # Never disclose transport/model errors or replay an unavailable result.
        finally:
            if tasks.get(key) == (expected, asyncio.current_task()):
                tasks.pop(key, None)

    pending = deliver()
    task = app.create_task(pending, name="intake-status-delivery")
    # PTB wraps the coroutine: cancellation before the wrapper's first step must
    # also close the inner coroutine (otherwise shutdown emits an unawaited warning).
    task.add_done_callback(lambda _finished: pending.close())
    tasks[key] = (expected, task)
