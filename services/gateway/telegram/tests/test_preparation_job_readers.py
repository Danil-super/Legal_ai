"""Intermediate queue results are not final legal reports or PDF offers."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from telegram.ext import ApplicationHandlerStop
from telegram_gateway.analysis_jobs_runtime import (
    JOBS_CLIENT_KEY,
    JobSnapshot,
    job_message,
    notify_completed_analyses,
    status_callback,
)

CASE, JOB, OPERATION, ESCALATION = uuid4(), uuid4(), uuid4(), uuid4()


def _snapshot(status="NEEDS_FACTS"):
    result = {
        "resultVersion": 2, "status": status,
        "source": {"operationId": str(OPERATION), "caseId": str(CASE),
                   "factSnapshotSha256": "a" * 64, "context": None},
    }
    if status == "NEEDS_FACTS":
        result.update(reasonCode="FACTUAL_CLARIFICATION_REQUIRED",
                      clarificationRequests=[{"questionId": "RESPONSE_PURPOSE"}])
    elif status == "LIMITED":
        result["reasonCode"] = "LEGAL_EVIDENCE_UNAVAILABLE"
    else:
        result.update(escalationId=str(ESCALATION), riskLevel="HIGH",
                      riskPolicyVersion="dental-risk.v4")
    return {"resultVersion": 2, "jobId": str(JOB), "caseId": str(CASE),
            "state": "SUCCEEDED", "errorCode": None, "result": result}


@pytest.mark.parametrize("status", ["NEEDS_FACTS", "LIMITED", "HUMAN_REVIEW"])
def test_intermediate_result_stays_typed_and_never_offers_a_pdf_or_fake_report(status):
    job = JobSnapshot.parse(_snapshot(status))
    assert job.result_version == 2
    text, keyboard = job_message(job)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert not any(value.startswith("analysis:pdf:") for value in callbacks)
    assert "Hermes" not in text
    assert "factSnapshot" not in text
    assert "report" not in job.result
    if status == "NEEDS_FACTS":
        assert "уточн" in text.lower()
    if status == "HUMAN_REVIEW":
        assert f"case:escalation:{ESCALATION}" in callbacks


@pytest.mark.parametrize("malformation", [
    "version", "float", "missing_version", "cross_case", "no_result", "wrong_state",
    "fake_report", "legacy_envelope", "error_on_success",
])
def test_preparation_job_parser_rejects_malformed_or_wrongly_versioned_results(malformation):
    payload = _snapshot()
    if malformation == "version":
        payload["resultVersion"] = 3
    elif malformation == "float":
        payload["resultVersion"] = 2.0
    elif malformation == "missing_version":
        payload["result"].pop("resultVersion")
    elif malformation == "cross_case":
        payload["result"]["source"]["caseId"] = str(uuid4())
    elif malformation == "no_result":
        payload["result"] = None
    elif malformation == "wrong_state":
        payload["state"] = "RUNNING"
    elif malformation == "fake_report":
        payload["result"]["report"] = None
    elif malformation == "legacy_envelope":
        payload.pop("resultVersion")
    else:
        payload["errorCode"] = "UNTRUSTED_FAILURE"
    with pytest.raises(ValueError):
        JobSnapshot.parse(payload)


class Core:
    def __init__(self, status):
        self.payload = _snapshot(status)
        self.acked = []
        self.status_calls = []

    async def status(self, job, actor):
        self.status_calls.append((job, actor))
        return deepcopy(self.payload)

    async def notifications(self):
        return {"items": [] if self.acked else [
            {"jobId": str(JOB), "telegramUserId": 777, "messageId": 456},
        ]}

    async def ack(self, job, actor):
        self.acked.append((job, actor))


@pytest.mark.parametrize("status", ["NEEDS_FACTS", "LIMITED", "HUMAN_REVIEW"])
def test_restart_notifier_delivers_and_acknowledges_intermediate_without_model_or_pdf(status):
    core = Core(status)
    context = SimpleNamespace(bot_data={JOBS_CLIENT_KEY: core}, bot=SimpleNamespace(
        edit_message_text=AsyncMock(), send_message=AsyncMock(),
    ))
    asyncio.run(notify_completed_analyses(context))
    asyncio.run(notify_completed_analyses(context))
    assert core.acked == [(JOB, 777)]
    context.bot.edit_message_text.assert_awaited_once()
    context.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize("action", ["result", "pdf"])
def test_stale_result_or_pdf_callback_rechecks_and_never_fabricates_a_report(action, monkeypatch):
    core = Core("NEEDS_FACTS")
    context = SimpleNamespace(bot_data={JOBS_CLIENT_KEY: core}, bot=SimpleNamespace(
        send_message=AsyncMock(), edit_message_text=AsyncMock(),
    ))
    query = SimpleNamespace(data=f"analysis:{action}:{JOB}", answer=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=777))

    async def resolve_only(context, *, resolve_report, **kwargs):
        await resolve_report()
        pytest.fail("an intermediate result must never resolve to a PDF identity")

    monkeypatch.setattr("telegram_gateway.report_pdf_delivery.queue_report_pdf", resolve_only)
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(status_callback(update, context))
    assert core.status_calls == [(JOB, 777)]
    assert context.bot.send_message.await_count == 1
    text = context.bot.send_message.await_args.kwargs["text"]
    assert "уточн" in text.lower() if action == "result" else "недоступен" in text.lower()
