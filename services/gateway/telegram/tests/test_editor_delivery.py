import asyncio
from unittest.mock import AsyncMock

from telegram_gateway.editor_delivery import EditorFileDeliveryQueue


class Application:
    running = True

    def create_task(self, coroutine, *, name):
        return asyncio.create_task(coroutine, name=name)


def test_editor_downloads_leave_callbacks_free_and_bound_duplicates_and_memory() -> None:
    async def scenario():
        application = Application()
        queue = EditorFileDeliveryQueue(application, max_pending=2)
        started = asyncio.Event()
        finish = asyncio.Event()
        second = AsyncMock()
        error = AsyncMock()

        async def blocked_download():
            started.set()
            await finish.wait()

        assert queue.submit(123, blocked_download, error) == "STARTED"
        await started.wait()
        assert queue.submit(123, second, error) == "DUPLICATE"
        assert queue.submit(456, second, error) == "STARTED"
        assert queue.submit(789, second, error) == "FULL"
        await asyncio.sleep(0)
        second.assert_not_awaited()
        # A queued PDF does not occupy this event loop or the update handler.
        unrelated_callback = AsyncMock()
        await unrelated_callback()
        unrelated_callback.assert_awaited_once()
        finish.set()
        await queue.drain()
        second.assert_awaited_once()
        error.assert_not_awaited()
        assert queue.pending_count == 0

    asyncio.run(scenario())


def test_editor_download_stop_cancels_running_io_and_does_not_send_late_messages() -> None:
    async def scenario():
        application = Application()
        queue = EditorFileDeliveryQueue(application)
        started = asyncio.Event()
        closed = asyncio.Event()
        error = AsyncMock()

        async def blocked_download():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        queue.submit(123, blocked_download, error)
        await started.wait()
        application.running = False
        await asyncio.wait_for(queue.drain(), timeout=1)
        assert closed.is_set()
        error.assert_not_awaited()
        assert queue.submit(456, blocked_download, error) == "STOPPED"

    asyncio.run(scenario())


def test_editor_download_deadline_releases_slot_and_reports_error() -> None:
    async def scenario():
        queue = EditorFileDeliveryQueue(Application(), timeout_seconds=0.01)
        error = AsyncMock()
        closed = asyncio.Event()

        async def blocked_download():
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        queue.submit(123, blocked_download, error)
        await queue.drain()
        assert closed.is_set()
        error.assert_awaited_once()
        assert queue.pending_count == 0

    asyncio.run(scenario())
