import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from telegram import Chat, Message, Update, User
from telegram_gateway import intake_delivery as delivery
from telegram_gateway.case_wizard import LegalCoreApiError


def setup_watch():
    actor = 1111
    selected_key = "understanding_intake"
    tasks, events = [], []
    operation, draft = uuid4(), uuid4()
    user = User(actor, "Synthetic", False)
    update = Update(1, message=Message(2, datetime.now(UTC), Chat(actor, "private"),
                                       from_user=user))
    app = SimpleNamespace(bot_data={}, user_data={actor: {}}, bot=None)

    def create_task(coro, **kwargs):
        task = asyncio.create_task(coro, **kwargs)
        tasks.append(task)
        return task

    async def process_update(event):
        events.append(event)

    async def serial(event, coroutine):
        await coroutine

    app.create_task = create_task
    app.process_update = process_update
    app.update_processor = SimpleNamespace(process_update=serial)
    context = SimpleNamespace(application=app)

    def select(op, draft_id):
        app.user_data[actor][selected_key] = {
            "operationId": str(op), "draftId": str(draft_id),
        }

    select(operation, draft)
    return actor, selected_key, operation, draft, update, app, context, tasks, events, select


def test_review_new_selected_operation_gets_its_own_watcher():
    async def scenario():
        _, key, operation, draft, update, app, context, tasks, events, select = setup_watch()
        calls = []

        async def get(op):
            calls.append(op)
            return SimpleNamespace(state="PROPOSED")

        with patch.object(delivery, "POLL_INTERVAL", 0.001):
            delivery.watch(update, context, selected_key=key, operation=operation,
                           draft_id=draft, client=SimpleNamespace(get=get), callback="old")
            newer_operation, newer_draft = uuid4(), uuid4()
            select(newer_operation, newer_draft)
            delivery.watch(update, context, selected_key=key, operation=newer_operation,
                           draft_id=newer_draft, client=SimpleNamespace(get=get), callback="new")
            await asyncio.gather(*tasks, return_exceptions=True)
        assert calls == [newer_operation]
        assert len(events) == 1 and events[0].callback_query.data == "new"
        assert not app.bot_data[delivery._KEY]
    asyncio.run(scenario())


def test_review_bounded_poll_and_duplicate_watch():
    async def scenario():
        _, key, operation, draft, update, app, context, tasks, events, _ = setup_watch()
        calls = []

        async def get(op):
            calls.append(op)
            return SimpleNamespace(state="PENDING")

        with patch.object(delivery, "POLL_INTERVAL", 0):
            for _ in range(5):
                delivery.watch(update, context, selected_key=key, operation=operation,
                               draft_id=draft, client=SimpleNamespace(get=get), callback="refresh")
            assert len(tasks) == 1
            await asyncio.gather(*tasks)
        assert len(calls) == delivery.MAX_POLLS == 90
        assert not events and not app.bot_data[delivery._KEY]
    asyncio.run(scenario())


def test_review_server_error_stops_without_retry_or_delivery():
    async def scenario():
        _, key, operation, draft, update, app, context, tasks, events, _ = setup_watch()
        calls = []

        async def get(op):
            calls.append(op)
            raise LegalCoreApiError(503, "UNAVAILABLE", "synthetic")

        with patch.object(delivery, "POLL_INTERVAL", 0):
            delivery.watch(update, context, selected_key=key, operation=operation,
                           draft_id=draft, client=SimpleNamespace(get=get), callback="refresh")
            await asyncio.gather(*tasks)
        assert calls == [operation]
        assert not events and not app.bot_data[delivery._KEY]
    asyncio.run(scenario())
