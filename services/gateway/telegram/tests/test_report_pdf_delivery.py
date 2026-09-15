"""Exercise queued report delivery through real PTB dispatch, without network traffic."""

import asyncio
from unittest.mock import patch
from uuid import UUID

import pytest
from telegram import Update
from telegram.ext import ExtBot
from telegram_gateway.analysis_jobs_runtime import JOBS_CLIENT_KEY, JobSnapshot, job_message
from telegram_gateway.analysis_runtime import build_application_with_analysis
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.editor_delivery import EditorFileDeliveryQueue

ACTOR = 777
JOB = UUID("00000000-0000-0000-0000-000000000444")
CASE = UUID("00000000-0000-0000-0000-000000000445")
REPORT = UUID("00000000-0000-0000-0000-000000000446")
PDF = b"%PDF-1.7\nsynthetic-test-pdf"


def result():
    return {
        "jobId": str(JOB),
        "caseId": str(CASE),
        "state": "SUCCEEDED",
        "result": {
            "analysisAllowed": False,
            "riskLevel": "UNAVAILABLE",
            "escalationRequired": False,
            "escalationId": None,
            "report": {
                "id": str(REPORT),
                "caseId": str(CASE),
                "reportJson": {"reportId": str(REPORT), "case": {"id": str(CASE)}},
            },
        },
    }


def test_completed_job_offers_a_report_pdf_button_with_an_opaque_job_pointer():
    buttons = job_message(JobSnapshot.parse(result()))[1].inline_keyboard
    assert any(button.callback_data == f"analysis:pdf:{JOB}" for row in buttons for button in row)


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "unauthorized",
        "missing",
        "mismatched",
        "lawyer",
        "pdf_denied",
        "revoked_while_waiting",
    ],
)
def test_report_pdf_queue_reauthorizes_preserves_state_and_leaves_menu_responsive(
    monkeypatch, mode
):
    monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://orchestrator.test")
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "synthetic-test-key-" * 4)

    async def scenario():
        sent = []
        started, finish, checked = asyncio.Event(), asyncio.Event(), asyncio.Event()
        release_previous_file = asyncio.Event()
        download_attempts = []

        class Core:
            denied = False

            async def status(self, job, actor):
                assert (job, actor) == (JOB, ACTOR)
                checked.set()
                if mode == "unauthorized" or self.denied:
                    raise LegalCoreApiError(403, "ACCESS_DENIED", "Synthetic denial")
                payload = result()
                if mode == "missing":
                    payload["result"]["report"] = None
                elif mode == "mismatched":
                    payload["result"]["report"]["caseId"] = str(REPORT)
                return payload

            async def get_escalation(self, escalation, actor):
                assert (escalation, actor) == (CASE, ACTOR)
                checked.set()
                return {
                    "escalationId": str(CASE),
                    "caseId": str(CASE),
                    "report": result()["result"]["report"]["reportJson"],
                }

            async def get_actor(self, actor):
                return {"role": "CLINIC_OWNER"}

            async def download_pdf(self, report, actor):
                assert (report, actor) == (REPORT, ACTOR)
                download_attempts.append((report, actor))
                if mode == "pdf_denied":
                    raise LegalCoreApiError(403, "PDF_NOT_AVAILABLE", "Synthetic PDF denial")
                started.set()
                await finish.wait()
                return PDF

        async def post(self, endpoint, data=None, **kwargs):
            if endpoint == "getMe":
                return {"id": 123, "is_bot": True, "first_name": "Test", "username": "test_bot"}
            if endpoint == "answerCallbackQuery":
                return True
            sent.append((endpoint, data))
            return {
                "message_id": 10,
                "date": 1,
                "chat": {"id": ACTOR, "type": "private"},
                "text": (data or {}).get("text", ""),
            }

        def callback(app, data, number):
            user = {"id": ACTOR, "is_bot": False, "first_name": "Tester"}
            return Update.de_json(
                {
                    "update_id": number,
                    "callback_query": {
                        "id": str(number),
                        "chat_instance": "test",
                        "from": user,
                        "message": {
                            "message_id": number,
                            "date": 1,
                            "from": user,
                            "chat": {"id": ACTOR, "type": "private"},
                        },
                        "data": data,
                    },
                },
                app.bot,
            )

        with patch.object(ExtBot, "_post", post):
            app = build_application_with_analysis("123456:unit_test_token_value_1234567890")
            core = Core()
            app.bot_data[JOBS_CLIENT_KEY] = core
            app.bot_data["legal_core_client"] = core
            await app.initialize()
            await app.start()
            try:
                if mode == "revoked_while_waiting":
                    queue = EditorFileDeliveryQueue(app)
                    app.bot_data["legal_editor_file_deliveries"] = queue

                    async def previous_file():
                        await release_previous_file.wait()

                    async def previous_error():
                        raise AssertionError("unrelated file failed")

                    queue.submit(888, previous_file, previous_error)
                app.user_data[ACTOR]["escalation_discussion_id"] = str(CASE)
                before = dict(app.user_data[ACTOR])
                action = f"esc:pdf:{CASE}" if mode == "lawyer" else f"analysis:pdf:{JOB}"
                await asyncio.wait_for(app.process_update(callback(app, action, 1)), 0.5)
                if mode == "revoked_while_waiting":
                    assert not checked.is_set(), "authorization ran before queued transfer began"
                    core.denied = True
                    release_previous_file.set()
                await asyncio.wait_for(checked.wait(), 0.5)
                assert app.user_data[ACTOR] == before
                if mode in {"success", "lawyer"}:
                    await asyncio.wait_for(started.wait(), 0.5)
                    assert not finish.is_set()
                    await asyncio.wait_for(app.process_update(callback(app, "menu", 2)), 0.5)
                    assert any(endpoint == "editMessageText" for endpoint, _ in sent)
                finish.set()
                queue = app.bot_data["legal_editor_file_deliveries"]
                await asyncio.wait_for(queue.drain(), 1)
                documents = [data for endpoint, data in sent if endpoint == "sendDocument"]
                if mode in {"success", "lawyer"}:
                    assert len(documents) == 1
                    assert documents[0]["document"].input_file_content == PDF
                    assert documents[0]["chat_id"] == ACTOR
                    assert app.user_data[ACTOR] == {}, "background file mutated the new menu state"
                else:
                    assert documents == []
                    assert not started.is_set()
                    assert bool(download_attempts) is (mode == "pdf_denied")
            finally:
                finish.set()
                release_previous_file.set()
                await app.stop()
                await app.shutdown()

    asyncio.run(scenario())
