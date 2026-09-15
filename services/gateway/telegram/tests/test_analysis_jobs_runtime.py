import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from telegram.error import BadRequest, TimedOut
from telegram_gateway.analysis_jobs_runtime import (
    JOBS_CLIENT_KEY,
    AnalysisJobsClient,
    JobSnapshot,
    enqueue_analysis,
    notify_completed_analyses,
)

JOB = UUID("00000000-0000-0000-0000-000000000444")
CASE = UUID("00000000-0000-0000-0000-000000000445")


class Server:
    def __init__(self):
        self.acked = []
        self.requests = []
        self.state = "QUEUED"

    def snapshot(self):
        return {
            "jobId": str(JOB),
            "caseId": str(CASE),
            "state": self.state,
            "errorCode": "LEGAL_EVIDENCE_UNAVAILABLE" if self.state == "FAILED" else None,
            "result": None,
        }

    async def enqueue(self, case, actor, message_id):
        self.requests.append((case, actor, message_id))
        return self.snapshot()

    async def status(self, job, actor):
        assert (job, actor) == (JOB, 777)
        return self.snapshot()

    async def notifications(self):
        return {
            "items": []
            if self.acked
            else [{"jobId": str(JOB), "telegramUserId": 777, "messageId": 123}]
        }

    async def ack(self, job, actor):
        self.acked.append((job, actor))


class Bot:
    def __init__(self, error=None):
        self.sent = []
        self.edited = []
        self.error = error

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=123)

    async def edit_message_text(self, **kwargs):
        if self.error:
            raise self.error
        self.edited.append(kwargs)


def context(server, bot):
    return SimpleNamespace(bot_data={JOBS_CLIENT_KEY: server}, bot=bot)


def test_analysis_callback_only_enqueues_and_targets_private_actor_chat():
    async def scenario():
        server, bot = Server(), Bot()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=777), effective_chat=SimpleNamespace(id=-999)
        )
        await enqueue_analysis(update, context(server, bot), CASE)
        assert server.requests == [(CASE, 777, 123)]
        assert bot.sent[0]["chat_id"] == 777
        assert (
            bot.edited[0]["reply_markup"].inline_keyboard[0][0].callback_data
            == f"analysis:status:{JOB}"
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error,acknowledged",
    [
        (None, True),
        (BadRequest("Message is not modified"), True),
        (BadRequest("Message to edit not found"), True),
        (BadRequest("Some unknown Telegram failure"), False),
        (TimedOut(), False),
    ],
)
def test_notifier_recovers_from_restart_and_only_acks_known_terminal_delivery(error, acknowledged):
    async def scenario():
        server, bot = Server(), Bot(error)
        server.state = "FAILED"
        await notify_completed_analyses(context(server, bot))
        assert bool(server.acked) is acknowledged
        if not acknowledged:
            # A new gateway process has no previous context but sees the persisted intent.
            recovered = Bot()
            await notify_completed_analyses(context(server, recovered))
            assert server.acked == [(JOB, 777)]
            assert len(recovered.edited) == 1
        assert bot.sent == [], (
            "recovery must edit the persisted message, not send duplicate results"
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change", [{"state": "MAYBE"}, {"jobId": "invalid"}, {"state": "SUCCEEDED", "result": None}]
)
def test_job_status_rejects_malformed_server_state(change):
    payload = Server().snapshot() | change
    with pytest.raises(ValueError):
        JobSnapshot.parse(payload)


def test_fast_worker_completion_cannot_be_overwritten_by_late_queued_edit():
    async def scenario():
        server, bot = Server(), Bot()
        ctx = context(server, bot)
        pending = None
        attempted = asyncio.Event()

        async def enqueue(case, actor, message_id):
            nonlocal pending
            queued = server.snapshot()
            server.state = "FAILED"

            async def completed():
                attempted.set()
                await notify_completed_analyses(ctx)

            pending = asyncio.create_task(completed())
            await attempted.wait()
            return queued

        server.enqueue = enqueue
        await enqueue_analysis(SimpleNamespace(effective_user=SimpleNamespace(id=777)), ctx, CASE)
        await pending
        assert "Кейс сохранён" in bot.edited[-1]["text"]
        assert server.acked == [(JOB, 777)]
        assert ctx.bot_data["analysis_message_locks"] == {}

    asyncio.run(scenario())


def test_rate_limit_keeps_intent_pending_and_defers_subsequent_notification_poll():
    from telegram.error import RetryAfter

    async def scenario():
        server, bot = Server(), Bot(RetryAfter(60))
        server.state = "FAILED"
        ctx = context(server, bot)
        await notify_completed_analyses(ctx)
        assert server.acked == []
        bot.error = None
        await notify_completed_analyses(ctx)
        assert bot.edited == []
        assert server.acked == []

    asyncio.run(scenario())


def test_jobs_http_contract_separates_actor_and_internal_credentials_and_accepts_empty_ack():
    import httpx2

    async def scenario():
        calls = []

        def handle(request):
            calls.append(request)
            if request.url.path.endswith("notification-ack"):
                return httpx2.Response(204)
            if "/internal/" in request.url.path:
                return httpx2.Response(200, json={"items": []})
            return httpx2.Response(202, json=Server().snapshot())

        async with httpx2.AsyncClient(
            base_url="http://core.test", transport=httpx2.MockTransport(handle)
        ) as http:
            client = AnalysisJobsClient(http, "synthetic-internal-key")
            await client.enqueue(CASE, 777, 123)
            await client.status(JOB, 777)
            await client.notifications()
            await client.ack(JOB, 777)
        assert calls[0].url.path == f"/v1/cases/{CASE}/analysis-jobs"
        assert calls[0].content == b'{"messageId":123}'
        for request in (calls[0], calls[1], calls[3]):
            assert request.headers["X-Telegram-User-Id"] == "777"
            assert "X-Agent-Internal-Key" not in request.headers
        assert calls[2].headers["X-Agent-Internal-Key"] == "synthetic-internal-key"
        assert "X-Telegram-User-Id" not in calls[2].headers

    asyncio.run(scenario())


def test_jobs_client_and_periodic_notifier_follow_application_lifecycle():
    from telegram_gateway.analysis_jobs_runtime import install_analysis_jobs
    from telegram_gateway.bot import build_application

    async def scenario():
        events = []
        app = build_application("123456:unit_test_token_value_1234567890")

        async def previous_init(application):
            events.append("init")

        async def previous_shutdown(application):
            events.append("shutdown")

        app.post_init, app.post_shutdown = previous_init, previous_shutdown
        install_analysis_jobs(app, "synthetic-internal-key")
        await app.post_init(app)
        client = app.bot_data[JOBS_CLIENT_KEY]
        assert len(app.job_queue.get_jobs_by_name("durable-analysis-notifications")) == 1
        await app.post_shutdown(app)
        assert events == ["init", "shutdown"]
        assert client.http.is_closed
        assert JOBS_CLIENT_KEY not in app.bot_data

    asyncio.run(scenario())
