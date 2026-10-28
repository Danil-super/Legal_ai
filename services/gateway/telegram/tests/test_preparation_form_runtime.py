"""Preparation offers a factual form, never an intermediate legal report."""

import asyncio
import os
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx2
import pytest
from sqlalchemy import text
from telegram.ext import ApplicationHandlerStop
from telegram_gateway import bot
from telegram_gateway.analysis_jobs_runtime import (
    JOBS_CLIENT_KEY,
    JobSnapshot,
    job_message,
    notify_completed_analyses,
    status_callback,
)
from telegram_gateway.case_wizard import LegalCoreClient
from telegram_gateway.clarification_intent import decode_preparation_callback
from telegram_gateway.clarification_runtime import create_clarification
from test_clarification_recovery import _source_rows
from test_preparation_analysis import _submission
from test_preparation_analysis import prepared_case as prepared_case
from test_preparation_job_readers import CASE, JOB, OPERATION, Core, _snapshot
from test_preparation_recovery_runtime import _context
from test_verified_copy_analysis_e2e import isolated_database as isolated_database


def _buttons(keyboard):
    return [button for row in keyboard.inline_keyboard for button in row]


@pytest.mark.parametrize("reason", [
    None, "INSUFFICIENT_FACTS", "ANALYSIS_DATE_UNCERTAIN", "LEGAL_EVIDENCE_UNAVAILABLE",
    "EVIDENCE_NOT_VERIFIED", "FACTUAL_SAFETY_UNCERTAIN",
])
def test_factual_preparation_offers_one_explicit_origin_bound_form(reason):
    payload = _snapshot("NEEDS_FACTS" if reason is None else "LIMITED")
    if reason is not None:
        payload["result"]["reasonCode"] = reason
    before = deepcopy(payload)
    message, keyboard = job_message(JobSnapshot.parse(payload))
    controls = _buttons(keyboard)
    factual = [button for button in controls if button.callback_data.startswith("case:clarify2:")]
    assert len(factual) == 1
    assert factual[0].text == ("Ответить" if reason is None else "Изменить сведения")
    assert decode_preparation_callback(factual[0].callback_data) == (CASE, OPERATION)
    assert len(factual[0].callback_data.encode()) <= 64
    assert not any(button.callback_data.startswith(("analysis:pdf:", "analysis:result:",
                                                     "case:analyze:", "case:clarify:"))
                   for button in controls)
    assert "Hermes" not in message and "operationId" not in message
    if reason is None:
        assert "уточните" in message.lower() or "Как" in message or "Когда" in message
    else:
        assert "не сформированы" in message.lower()
    assert payload == before


def test_human_review_does_not_delay_escalation_with_an_optional_factual_form():
    _, keyboard = job_message(JobSnapshot.parse(_snapshot("HUMAN_REVIEW")))
    callbacks = [button.callback_data for button in _buttons(keyboard)]
    assert any(value.startswith("case:escalation:") for value in callbacks)
    assert not any(value.startswith("case:clarify") for value in callbacks)


def test_presentation_rejects_a_result_from_another_case_even_without_envelope_parser():
    job = JobSnapshot(JOB, uuid4(), "SUCCEEDED", None, _snapshot()["result"], result_version=2)
    with pytest.raises(ValueError, match="case mismatch"):
        job_message(job)


@pytest.mark.parametrize("status", ["NEEDS_FACTS", "LIMITED"])
@pytest.mark.parametrize("delivery", ["status", "result", "notifier"])
def test_all_delivery_paths_keep_the_same_control_without_opening_a_form(status, delivery):
    core = Core(status)
    context = SimpleNamespace(bot_data={JOBS_CLIENT_KEY: core}, bot=SimpleNamespace(
        edit_message_text=AsyncMock(), send_message=AsyncMock(),
    ), user_data={})
    if delivery == "notifier":
        asyncio.run(notify_completed_analyses(context))
        asyncio.run(notify_completed_analyses(context))
        context.bot.edit_message_text.assert_awaited_once()
        context.bot.send_message.assert_not_awaited()
        assert core.acked == [(JOB, 777)]
        delivered = context.bot.edit_message_text.await_args.kwargs
    else:
        update = SimpleNamespace(effective_user=SimpleNamespace(id=777), callback_query=(
            SimpleNamespace(data=f"analysis:{delivery}:{JOB}", answer=AsyncMock(), message=None)
        ))
        with pytest.raises(ApplicationHandlerStop):
            asyncio.run(status_callback(update, context))
        context.bot.send_message.assert_awaited_once()
        context.bot.edit_message_text.assert_not_awaited()
        delivered = context.bot.send_message.await_args.kwargs
    control = next(button for button in _buttons(delivered["reply_markup"])
                   if button.callback_data.startswith("case:clarify2:"))
    assert decode_preparation_callback(control.callback_data) == (CASE, OPERATION)
    assert delivered["chat_id"] == 777
    assert context.user_data == {}


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="isolated PostgreSQL")
@pytest.mark.parametrize("questions", [True, False])
def test_rendered_form_button_reaches_owned_durable_draft_without_a_report(
    prepared_case, questions,
):
    core, actor, _, case, headers, context_data, engine = prepared_case
    response = core.post(f"/v1/cases/{case}/preparation-submissions", headers=headers,
                         json=_submission(context_data, questions=questions))
    assert response.status_code == 201, response.text
    result = response.json()
    snapshot = JobSnapshot.parse({
        "resultVersion": 2, "jobId": str(uuid4()), "caseId": case,
        "state": "SUCCEEDED", "errorCode": None, "result": result,
    })
    _, keyboard = job_message(snapshot)
    button = next(button for button in _buttons(keyboard)
                  if button.callback_data.startswith("case:clarify2:"))
    assert decode_preparation_callback(button.callback_data) == (
        UUID(case), UUID(result["source"]["operationId"]),
    )
    before = _source_rows(case)
    update, context, _, message = _context()
    update.effective_user.id = actor
    update.callback_query.data = button.callback_data

    def bridge(request):
        actual = core.request(request.method, str(request.url), headers=dict(request.headers),
                              content=request.content)
        return httpx2.Response(actual.status_code, content=actual.content,
                               headers={"Content-Type": "application/json"})

    async def run():
        async with httpx2.AsyncClient(base_url="http://core.test",
                                     transport=httpx2.MockTransport(bridge)) as http:
            context.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = LegalCoreClient(http)
            with pytest.raises(ApplicationHandlerStop):
                await create_clarification(update, context)

    asyncio.run(run())
    selected = _buttons(message.reply_text.await_args.kwargs["reply_markup"])[0].callback_data
    with engine.connect() as connection:
        draft = connection.execute(text(
            "SELECT id,draft_json FROM telegram_intake_drafts"
        )).one()
        assert selected == f"case:draft:{draft.id}"
        assert draft.draft_json["correctionSource"]["operationId"] == (
            result["source"]["operationId"]
        )
    assert _source_rows(case) == before
    assert before["case_reports"] == []
