"""Both interpretation kinds share one bounded runner and durable cancellation."""

import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest

from legal_core.intake_answer_operations import cancel_answer, read_answer, reserve_answer
from legal_core.intake_preparation_runner import IntakePreparationRunner
from legal_core.model_admission import ModelAdmission
from test_intake_preparation_lifecycle import _run, draft_owner
from test_intake_preparation_runner import SETTINGS

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]
ANSWER = "Пациент сообщил о сколе коронки после лечения."


@pytest.mark.parametrize("outcome", ["success", "unresolved", "busy", "cancel", "shutdown",
                                     "early_shutdown", "binding", "timeout", "protocol"])
def test_answer_runner_one_call_current_lease_and_shared_capacity(draft_owner, outcome):
    actor, _, _, draft = draft_owner

    async def scenario(sessions):
        admission = ModelAdmission()
        if outcome == "busy":
            assert await admission.try_acquire() and await admission.try_acquire()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        claim = await reserve_answer(sessions, telegram_user_id=actor, draft_id=draft["id"],
                                     expected_revision=draft["revision"], answer=ANSWER)

        async def handle(request):
            calls.append(request)
            entered.set()
            await release.wait()
            if outcome == "timeout":
                raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_MARKER", request=request)
            assert request.url.path.endswith("/interpret")
            assert set(json.loads(request.content)) == {
                "draftId", "leaseToken", "expectedRevision", "expectedInputSha256",
            }
            unresolved = outcome == "unresolved"
            return httpx.Response(200, json={
                "operationId": str(claim.response.operation_id),
                "draftId": str(uuid4()) if outcome == "binding" else draft["id"],
                "draftRevision": draft["revision"], "inputSha256": claim.intent.input_sha256,
                "answerSha256": claim.intent.answer_sha256,
                "questionSha256": claim.intent.question_sha256,
                "interpretation": {
                    "status": "UNRESOLVED" if unresolved else "PROPOSED",
                    "value": None if unresolved else "Wrong ungrounded text" if (
                        outcome == "protocol"
                    ) else ANSWER,
                    "sourceSpans": [] if unresolved else [{"start": 0, "end": len(ANSWER)}],
                    "reasonCode": "AMBIGUOUS" if unresolved else None,
                },
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            runner = IntakePreparationRunner(sessions, SETTINGS, admission, client=client)
            try:
                await runner.submit(telegram_user_id=actor, operation=claim)
                # A replay must never schedule another model call.
                await runner.submit(telegram_user_id=actor, operation=claim)
                if outcome not in {"busy", "early_shutdown"}:
                    await asyncio.wait_for(entered.wait(), timeout=2)
                if outcome == "cancel":
                    await cancel_answer(sessions, telegram_user_id=actor,
                                        operation_id=claim.response.operation_id)
                    runner.cancel(claim.response.operation_id)
                if outcome in {"shutdown", "early_shutdown"}:
                    await runner.close()
                release.set()
                await asyncio.gather(*runner._tasks, return_exceptions=True)
                result = await read_answer(sessions, telegram_user_id=actor,
                                           operation_id=claim.response.operation_id)
                expected = {"success": "PROPOSED", "unresolved": "UNRESOLVED"}.get(
                    outcome, "MANUAL_CONTINUATION",
                )
                assert result.response.state == expected
                assert len(calls) == (0 if outcome in {"busy", "early_shutdown"} else 1)
            finally:
                release.set()
                await runner.close()
        if outcome == "busy":
            admission.release()
            admission.release()
        assert await admission.try_acquire() and await admission.try_acquire()
        assert not await admission.try_acquire()
        admission.release()
        admission.release()

    _run(scenario)
