"""The durable completion message becomes the PDF instead of repeating its contents."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import TimedOut
from telegram.request._requestparameter import RequestParameter
from telegram_gateway.analysis_jobs_runtime import (
    JOBS_CLIENT_KEY,
    JobSnapshot,
    job_message,
    notify_completed_analyses,
)
from telegram_gateway.bot import LEGAL_CORE_CLIENT_KEY
from test_case_preparation_contracts import _ready


def snapshot():
    result = _ready()
    return {"resultVersion": 2, "jobId": result["source"]["operationId"],
            "caseId": result["source"]["caseId"], "state": "SUCCEEDED", "result": result}


def test_final_message_is_short_and_has_no_report_text_or_result_button():
    text, keyboard = job_message(JobSnapshot.parse(snapshot()))
    assert len(text) < 180 and "PDF" in text
    assert "Зафиксировать синтетическое обращение" not in text
    assert not any(b.callback_data.startswith("analysis:result:")
                   for row in keyboard.inline_keyboard for b in row)
    assert any(b.callback_data == "history:list:0"
               for row in keyboard.inline_keyboard for b in row)


@pytest.mark.parametrize("failure", [False, True])
def test_notifier_attaches_authorized_pdf_to_same_message_before_ack(failure):
    async def run():
        wire = snapshot()
        jobs = SimpleNamespace(
            notifications=AsyncMock(return_value={"items": [{
                "jobId": wire["jobId"], "telegramUserId": 777, "messageId": 123,
            }]}), status=AsyncMock(return_value=wire), ack=AsyncMock(),
        )
        core = SimpleNamespace(download_pdf=AsyncMock(return_value=b"%PDF-1.7\nsynthetic"))
        bot = SimpleNamespace(edit_message_text=AsyncMock(), edit_message_media=AsyncMock(),
                              send_message=AsyncMock(), send_document=AsyncMock())
        if failure:
            bot.edit_message_media.side_effect = TimedOut()
        context = SimpleNamespace(bot_data={JOBS_CLIENT_KEY: jobs, LEGAL_CORE_CLIENT_KEY: core},
                                  bot=bot)
        await notify_completed_analyses(context)
        core.download_pdf.assert_awaited_once()
        bot.edit_message_media.assert_awaited_once()
        args = bot.edit_message_media.await_args.kwargs
        assert (args["chat_id"], args["message_id"]) == (777, 123)
        assert args["media"].media.input_file_content.startswith(b"%PDF-")
        wire = RequestParameter.from_input("media", args["media"])
        assert json.loads(wire.json_value)["media"].startswith("attach://")
        assert wire.multipart_data
        assert len(args["media"].caption) < 180
        assert jobs.ack.await_count == (0 if failure else 1)
        bot.send_document.assert_not_awaited()  # Replay edits the durable message, no new copy.
        if failure:
            bot.edit_message_text.assert_awaited_once()
            fallback = bot.edit_message_text.await_args.kwargs
            assert len(fallback["text"]) < 180
            assert any(b.callback_data.startswith("analysis:pdf:")
                       for row in fallback["reply_markup"].inline_keyboard for b in row)
        else:
            bot.edit_message_text.assert_not_awaited()

    asyncio.run(run())
