"""Dispatcher latency with five externally blocked analyses, without Telegram/LLM traffic."""

import asyncio
import time
from unittest.mock import patch
from uuid import uuid4

from telegram import Update
from telegram.ext import ExtBot
from telegram_gateway.analysis_jobs_runtime import JOBS_CLIENT_KEY
from telegram_gateway.quick_intake_runtime import build_application_with_quick_intake
from test_dialog_isolation import Core


def test_menu_p95_stays_below_one_second_with_five_pending_analyses(monkeypatch):
    monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://agent-orchestrator:8010")
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "synthetic-key-" * 4)

    async def scenario():
        release_provider = asyncio.Event()
        pending = []

        class SlowAnalysisCore(Core):
            async def enqueue(self, case, actor, message_id):
                pending.append(asyncio.create_task(release_provider.wait()))
                self.jobs.append((case, actor, message_id))
                return {
                    "jobId": str(uuid4()),
                    "caseId": str(case),
                    "state": "QUEUED",
                    "result": None,
                    "errorCode": None,
                }

        async def fake_post(self, endpoint, data=None, **kwargs):
            if endpoint == "getMe":
                return {"id": 123, "is_bot": True, "first_name": "Test", "username": "test_bot"}
            if endpoint == "answerCallbackQuery":
                return True
            return {
                "message_id": 10,
                "date": 1,
                "chat": {"id": (data or {}).get("chat_id", 777), "type": "private"},
                "text": (data or {}).get("text", ""),
            }

        with patch.object(ExtBot, "_post", fake_post):
            app = build_application_with_quick_intake("123456:unit_test_token_value_1234567890")
            core = SlowAnalysisCore()
            app.bot_data["legal_core_client"] = core
            app.bot_data[JOBS_CLIENT_KEY] = core
            errors = []

            async def record_error(update, context):
                errors.append(context.error)

            app.add_error_handler(record_error)
            await app.initialize()
            counter = 0

            async def dispatch(actor, callback):
                nonlocal counter
                counter += 1
                user = {"id": actor, "is_bot": False, "first_name": "Synthetic"}
                payload = {
                    "update_id": counter,
                    "callback_query": {
                        "id": str(counter),
                        "chat_instance": "test",
                        "from": user,
                        "message": {
                            "message_id": counter,
                            "date": 1,
                            "from": user,
                            "chat": {"id": actor, "type": "private"},
                        },
                        "data": callback,
                    },
                }
                await asyncio.wait_for(app.process_update(Update.de_json(payload, app.bot)), 1)

            try:
                for actor in range(777, 782):
                    await dispatch(actor, f"case:analyze:{uuid4()}")
                assert len(pending) == 5 and not any(task.done() for task in pending)
                latencies = []
                for index in range(50):
                    started = time.perf_counter()
                    await dispatch(777 + index % 5, "menu")
                    latencies.append(time.perf_counter() - started)
                p95 = sorted(latencies)[47]
                assert p95 < 1.0
                assert not any(task.done() for task in pending)
                assert not errors
                print(
                    f"Synthetic dispatcher menu p95={p95 * 1000:.2f}ms; "
                    "50 updates, 5 pending analyses; external network latency excluded"
                )
            finally:
                release_provider.set()
                await asyncio.gather(*pending)
                await app.shutdown()

    asyncio.run(scenario())
