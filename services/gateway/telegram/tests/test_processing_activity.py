"""Processing activity must reflect live work without changing any result or consent."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from telegram.error import Forbidden, RetryAfter, TimedOut
from telegram.ext import ExtBot
from telegram_gateway import analysis_jobs_runtime as jobs
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.processing_activity import TypingPulse
from telegram_gateway.understanding_intent import bound_callback
from test_analysis_jobs_runtime import CASE, JOB, Bot, Server, context
from test_confirmed_understanding_runtime import TEXT, active_harness
from test_dialog_isolation import DRAFT
from test_intake_dialogue_delivery import await_proposal


class ActivityBot(Bot):
    def __init__(self):
        super().__init__()
        self.actions = []

    async def send_chat_action(self, **kwargs):
        self.actions.append(kwargs)
        return True


def test_pending_intake_sends_native_typing_without_another_user_message():
    async def scenario():
        visible = asyncio.Event()
        actions = []

        async def typing(_bot, **kwargs):
            actions.append(kwargs)
            visible.set()
            return True

        with patch.object(ExtBot, "send_chat_action", typing), patch(
            "telegram_gateway.intake_delivery.POLL_INTERVAL", 0.01,
        ):
            async with active_harness() as (_app, core, _sent, dispatch):
                await dispatch("quick:start")
                await dispatch("text:" + TEXT)
                await asyncio.wait_for(visible.wait(), timeout=0.5)
                assert actions[0]["action"] == "typing"
                assert actions[0]["chat_id"] > 0
                assert len(core.starts) == 1 and not core.accepts and not core.jobs
                core.propose(incomingKind="COMPLAINT")
                await await_proposal(_sent, bound_callback("accept", core.starts[0], DRAFT))
                completed_count = len(actions)
                await asyncio.sleep(0.03)
                assert len(actions) == completed_count

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [Forbidden("unavailable"), TimedOut(), RetryAfter(60)])
def test_native_indicator_failure_is_bounded_and_does_not_affect_analysis(error):
    async def scenario():
        bot = ActivityBot()
        calls = []

        async def rejected(**kwargs):
            calls.append(kwargs)
            raise error

        bot.send_chat_action = rejected
        pulse = TypingPulse()
        await pulse.send(bot, 777)
        await pulse.send(bot, 777)
        assert len(calls) == 1
        assert pulse.next_at > 0 and not bot.sent

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["denied", "missing", "wrong-job", "wrong-case", "offline"])
def test_report_activity_never_runs_for_an_unverified_or_inaccessible_job(failure):
    async def scenario():
        server, bot = Server(), ActivityBot()
        ctx = context(server, bot)
        await jobs.enqueue_analysis(SimpleNamespace(effective_user=SimpleNamespace(id=777)),
                                    ctx, CASE)

        async def changed_status(_job, _actor):
            if failure in {"wrong-job", "wrong-case"}:
                return server.snapshot() | {
                    "jobId" if failure == "wrong-job" else "caseId": str(uuid4()),
                }
            raise LegalCoreApiError({"denied": 403, "missing": 404, "offline": 503}[failure],
                                    "UNAVAILABLE", "unavailable")

        server.status = changed_status
        for _ in range(3):
            await jobs.notify_analysis_activity(ctx)
        assert not bot.actions and not server.acked
        assert ctx.bot_data[jobs.ACTIVITY_KEY] == {}
        assert server.requests == [(CASE, 777, 123)]

    asyncio.run(scenario())


def test_a_late_activity_read_cannot_replace_a_newer_case_for_the_same_actor():
    async def scenario():
        server, bot = Server(), ActivityBot()
        ctx = context(server, bot)
        await jobs.enqueue_analysis(SimpleNamespace(effective_user=SimpleNamespace(id=777)),
                                    ctx, CASE)
        entered, release = asyncio.Event(), asyncio.Event()
        old = server.snapshot()

        async def delayed(_job, _actor):
            entered.set()
            await release.wait()
            return old

        server.status = delayed
        reading = asyncio.create_task(jobs.notify_analysis_activity(ctx))
        await entered.wait()
        next_job, next_case = uuid4(), uuid4()
        server.snapshot = lambda: old | {"jobId": str(next_job), "caseId": str(next_case)}
        await jobs.enqueue_analysis(SimpleNamespace(effective_user=SimpleNamespace(id=777)),
                                    ctx, next_case)
        release.set()
        await reading
        assert not bot.actions
        assert ctx.bot_data[jobs.ACTIVITY_KEY][777].job.job_id == next_job

    asyncio.run(scenario())


def test_expired_activity_is_retired_without_polling_or_restarting_analysis():
    async def scenario():
        server, bot = Server(), ActivityBot()
        ctx = context(server, bot)
        await jobs.enqueue_analysis(SimpleNamespace(effective_user=SimpleNamespace(id=777)),
                                    ctx, CASE)
        ctx.bot_data[jobs.ACTIVITY_KEY][777].expires = 0

        async def forbidden_poll(*_args):
            raise AssertionError("expired activity must not make a request")

        server.status = forbidden_poll
        await jobs.notify_analysis_activity(ctx)
        assert not bot.actions and ctx.bot_data[jobs.ACTIVITY_KEY] == {}

    asyncio.run(scenario())


def test_report_activity_targets_only_requesting_actor_and_stops_when_work_finishes():
    async def scenario():
        server, bot = Server(), ActivityBot()
        ctx = context(server, bot)
        await jobs.enqueue_analysis(
            SimpleNamespace(effective_user=SimpleNamespace(id=777)), ctx, CASE,
        )
        await jobs.notify_analysis_activity(ctx)
        assert [(a["chat_id"], a["action"]) for a in bot.actions] == [(777, "typing")]
        assert server.requests == [(CASE, 777, 123)] and server.acked == []
        server.state = "FAILED"
        await jobs.notify_analysis_activity(ctx)
        assert len(bot.actions) == 1
        assert ctx.bot_data.get("analysis_activity", {}) == {}
        assert JOB not in server.acked

    asyncio.run(scenario())
