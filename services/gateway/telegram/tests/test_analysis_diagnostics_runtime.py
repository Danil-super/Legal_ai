# ruff: noqa: RUF001
"""Read-only diagnostics must not monopolize the sequential Telegram dispatcher."""

import asyncio
import inspect
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx2
import pytest
from telegram import Update
from telegram.error import BadRequest, TimedOut
from telegram.ext import ExtBot
from telegram_gateway import analysis_diagnostics_runtime as runtime
from telegram_gateway.update_processor import ActorSerialUpdateProcessor


def payload():
    return {
        "runtime": "DISABLED", "riskPolicy": "NOT_READY", "legalCorpusToday": "EMPTY",
        "checkedAt": "2026-09-21T14:00:00+00:00", "lastSuccessfulReportAt": None,
        "sourceRevision": "a" * 40,
    }


@asynccontextmanager
async def running_application(monkeypatch, *, failure=None):
    calls = []
    errors = []
    failed = False

    async def fake_post(self, endpoint, data=None, **kwargs):
        nonlocal failed
        data = dict(data or {})
        calls.append((endpoint, data))
        if endpoint == "getMe":
            return {"id": 123, "is_bot": True, "first_name": "Test", "username": "test_bot"}
        if endpoint == failure and not failed:
            failed = True
            if endpoint == "answerCallbackQuery":
                raise BadRequest("Synthetic expired callback")
            raise TimedOut("Synthetic delivery timeout")
        if endpoint == "answerCallbackQuery":
            return True
        return {
            "message_id": data.get("message_id", 1000 + len(calls)), "date": 1,
            "chat": {"id": data.get("chat_id", 777), "type": "private"},
            "text": data.get("text", ""),
        }

    monkeypatch.setattr(ExtBot, "_post", fake_post)
    # Menu authorization/transport is independent of the diagnostic request under test.
    monkeypatch.setattr(runtime.gateway_bot, "_main_menu_for_actor", AsyncMock(
        return_value=runtime.back_keyboard(),
    ))
    app = runtime.build_application_with_diagnostics("123456:unit_test_token_value_1234567890")

    async def record_error(update, context):
        errors.append(type(context.error).__name__)

    app.add_error_handler(record_error)
    await app.initialize()
    await app.start()
    try:
        yield app, calls, errors
    finally:
        tasks = list(app.bot_data.get(runtime.DIAGNOSTICS_TASKS_KEY, {}).values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.stop()
        await app.shutdown()


def update_for(app, counter, *, actor=777, command="/analysis_status", callback=None,
               chat_type="private"):
    user = {"id": actor, "is_bot": False, "first_name": "Synthetic"}
    message = {
        "message_id": counter, "date": 1, "from": user,
        "chat": {"id": actor, "type": chat_type}, "text": command,
        "entities": [{"type": "bot_command", "offset": 0, "length": len(command)}],
    }
    body = {"update_id": counter}
    if callback is None:
        body["message"] = message
    else:
        body["callback_query"] = {
            "id": str(counter), "chat_instance": "synthetic", "from": user,
            "message": message, "data": callback,
        }
    return Update.de_json(body, app.bot)


async def drain(app):
    tasks = list(app.bot_data.get(runtime.DIAGNOSTICS_TASKS_KEY, {}).values())
    await asyncio.wait_for(asyncio.gather(*tasks), 1)
    await asyncio.sleep(0)
    assert not app.bot_data.get(runtime.DIAGNOSTICS_TASKS_KEY)


def test_progress_menu_and_other_users_work_while_core_waits(monkeypatch):
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_core(actor):
            entered.set()
            await release.wait()
            return runtime.render_analysis_diagnostics(payload())

        fetch = AsyncMock(side_effect=slow_core)
        monkeypatch.setattr(runtime, "_fetch_diagnostics_text", fetch)
        async with running_application(monkeypatch) as (app, calls, errors):
            assert isinstance(app.update_processor, ActorSerialUpdateProcessor)
            await asyncio.wait_for(app.process_update(update_for(app, 1)), 0.5)
            await asyncio.wait_for(entered.wait(), 0.5)
            progress = next(data for method, data in calls
                            if method == "sendMessage" and data["text"] == runtime._PROGRESS_TEXT)
            assert not release.is_set()
            await asyncio.wait_for(app.process_update(update_for(app, 2, command="/menu")), 0.5)
            await asyncio.wait_for(app.process_update(update_for(
                app, 3, actor=778, command="/whoami",
            )), 0.5)
            assert any(method == "sendPhoto" for method, _ in calls)
            assert any(data.get("chat_id") == 778 and "778" in data.get("text", "")
                       for _, data in calls)
            assert fetch.await_count == 1
            # A command and a refresh callback coalesce into the same pending diagnosis.
            await app.process_update(update_for(app, 4))
            await app.process_update(update_for(app, 5, callback="analysis:service-status"))
            assert fetch.await_count == 1
            release.set()
            await drain(app)
            edits = [data for method, data in calls if method == "editMessageText"]
            assert len(edits) == 1
            assert edits[0]["chat_id"] == progress["chat_id"] == 777
            assert edits[0]["message_id"] != 2  # Never overwrite the menu/navigation message.
            assert "ДИАГНОСТИКА" in edits[0]["text"]
            assert not errors

    asyncio.run(scenario())


def test_two_actors_have_separate_results_and_refresh_reauthorizes(monkeypatch):
    async def scenario():
        fetch = AsyncMock(side_effect=lambda actor: f"Synthetic diagnosis for {actor}")
        monkeypatch.setattr(runtime, "_fetch_diagnostics_text", fetch)
        async with running_application(monkeypatch) as (app, calls, errors):
            await app.process_update(update_for(app, 1))
            await app.process_update(update_for(app, 2, actor=778))
            await drain(app)
            for method, data in calls:
                if method == "editMessageText":
                    assert data["text"] == f"Synthetic diagnosis for {data['chat_id']}"
            await app.process_update(update_for(app, 3))
            await drain(app)
            assert [call.args[0] for call in fetch.await_args_list] == [777, 778, 777]
            assert not errors

    asyncio.run(scenario())


def test_pending_work_is_bounded_and_early_cancellation_releases_slot(monkeypatch):
    async def scenario():
        monkeypatch.setattr(runtime, "MAX_PENDING_DIAGNOSTICS", 1)
        work_items = []
        original = runtime._deliver_analysis_diagnostics

        def track_work(update, actor):
            work = original(update, actor)
            work_items.append(work)
            return work

        monkeypatch.setattr(runtime, "_deliver_analysis_diagnostics", track_work)
        fetch = AsyncMock(return_value="Synthetic diagnosis")
        monkeypatch.setattr(runtime, "_fetch_diagnostics_text", fetch)
        async with running_application(monkeypatch) as (app, calls, errors):
            await app.process_update(update_for(app, 1))
            tasks = app.bot_data[runtime.DIAGNOSTICS_TASKS_KEY]
            first = tasks[(777, 777)]
            # No await between scheduling and cancellation: coroutine has not started.
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            await asyncio.sleep(0)
            assert not tasks
            assert inspect.getcoroutinestate(work_items[0]) == inspect.CORO_CLOSED
            fetch.assert_not_awaited()
            await app.process_update(update_for(app, 2))
            await app.process_update(update_for(app, 3, actor=778))
            await drain(app)
            fetch.assert_awaited_once_with(777)
            assert any("занята" in data.get("text", "") for _, data in calls)
            assert not errors

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["answerCallbackQuery", "sendMessage", "editMessageText"])
def test_telegram_failure_does_not_suppress_result_or_leak_task(monkeypatch, failure):
    async def scenario():
        fetch = AsyncMock(return_value="Synthetic diagnosis")
        monkeypatch.setattr(runtime, "_fetch_diagnostics_text", fetch)
        async with running_application(monkeypatch, failure=failure) as (app, calls, errors):
            await app.process_update(update_for(app, 1, callback="analysis:service-status"))
            await drain(app)
            assert calls[-1][1].get("text") == "Synthetic diagnosis"
            fetch.assert_awaited_once_with(777)
            assert not errors

    asyncio.run(scenario())


def test_group_command_never_starts_diagnostics(monkeypatch):
    async def scenario():
        fetch = AsyncMock(side_effect=AssertionError("no diagnosis in a shared chat"))
        monkeypatch.setattr(runtime, "_fetch_diagnostics_text", fetch)
        async with running_application(monkeypatch) as (app, calls, errors):
            await app.process_update(update_for(app, 1, chat_type="group"))
            fetch.assert_not_called()
            assert runtime.DIAGNOSTICS_TASKS_KEY not in app.bot_data
            assert any("личный чат" in data.get("text", "") for _, data in calls)
            assert not errors

    asyncio.run(scenario())


def transport(monkeypatch, handle):
    real_client = httpx2.AsyncClient

    def factory(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx2.MockTransport(handle), **kwargs)

    monkeypatch.setattr(runtime.httpx2, "AsyncClient", factory)


def test_transport_uses_only_read_only_authorized_core_endpoint(monkeypatch):
    def handle(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/analysis-diagnostics"
        assert request.headers["X-Telegram-User-Id"] == "777"
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx2.Response(200, json=payload())

    transport(monkeypatch, handle)
    text = asyncio.run(runtime._fetch_diagnostics_text(777))
    assert "ДИАГНОСТИКА" in text and "не вызывает модели" in text


@pytest.mark.parametrize("status,expected", [
    (403, "владельцу"), (404, "одной версией"), (500, "Не удалось"), (302, "Не удалось"),
])
def test_http_errors_are_not_model_results_and_redirects_are_not_followed(
    monkeypatch, status, expected,
):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(status, text="SYNTHETIC_PRIVATE_BODY", headers={
            "location": "http://must-not-be-contacted.invalid",
        })

    transport(monkeypatch, handle)
    text = asyncio.run(runtime._fetch_diagnostics_text(777))
    assert expected in text and "SYNTHETIC_PRIVATE_BODY" not in text
    assert len(requests) == 1


@pytest.mark.parametrize("bad", [[], {"runtime": "unexpected"}, "invalid"])
def test_malformed_responses_fail_safely(monkeypatch, bad):
    transport(monkeypatch, lambda request: httpx2.Response(200, json=bad))
    assert asyncio.run(runtime._fetch_diagnostics_text(777)) == runtime._UNAVAILABLE_TEXT


def test_wall_timeout_cancels_dripping_body_and_closes_stream(monkeypatch):
    class Drip(httpx2.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.01)
                yield b" "

        async def aclose(self):
            self.closed = True

    stream = Drip()
    monkeypatch.setattr(runtime, "DIAGNOSTICS_TIMEOUT_SECONDS", 0.05)
    transport(monkeypatch, lambda request: httpx2.Response(200, stream=stream))
    assert asyncio.run(runtime._fetch_diagnostics_text(777)) == runtime._TIMEOUT_TEXT
    assert stream.closed


@pytest.mark.parametrize("compressed", [False, True])
def test_response_limits_apply_before_buffering_or_decompression(monkeypatch, compressed):
    class Body(httpx2.AsyncByteStream):
        read_count = 0
        closed = False

        async def __aiter__(self):
            for _ in range(4):
                self.read_count += 1
                yield b"x" * 8192

        async def aclose(self):
            self.closed = True

    stream = Body()
    headers = {"content-encoding": "gzip"} if compressed else {"content-length": "1"}
    transport(monkeypatch, lambda request: httpx2.Response(200, stream=stream, headers=headers))
    assert asyncio.run(runtime._fetch_diagnostics_text(777)) == runtime._UNAVAILABLE_TEXT
    assert stream.read_count == (0 if compressed else 3)
    assert stream.closed


def test_timeout_is_delivered_as_final_message(monkeypatch):
    async def scenario():
        async def slow(request):
            await asyncio.Event().wait()

        monkeypatch.setattr(runtime, "DIAGNOSTICS_TIMEOUT_SECONDS", 0.05)
        async with running_application(monkeypatch) as (app, calls, errors):
            transport(monkeypatch, slow)
            await app.process_update(update_for(app, 1))
            await drain(app)
            assert any(method == "editMessageText" and data["text"] == runtime._TIMEOUT_TEXT
                       for method, data in calls)
            assert not errors

    asyncio.run(scenario())
