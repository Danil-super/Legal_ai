"""The model receives server-bound answer context, never client supplied source."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from agent_orchestrator.legal_core_client import (
    LegalCoreClient,
    LegalCoreEndpoint,
    LegalCoreError,
    LegalCoreProtocolError,
)
from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_answer_intent import AnswerPreparationIntent
from legal_core.case_api import _canonical_hash
from legal_core.intake_preparation_contracts import UnderstandingContextRequest

ANSWER = "Это письменная претензия"


def scenario(change=None, *, failure=None):
    operation, draft, token = uuid4(), uuid4(), uuid4()
    question = derive_answer_question({"intakeVersion": 2}, "INCOMING")
    binding = UnderstandingContextRequest(
        leaseToken=token, expectedRevision=2, expectedInputSha256="a" * 64,
    )
    intent = AnswerPreparationIntent(
        clinicId=uuid4(), actorMembershipId=uuid4(), draftId=draft, draftRevision=2,
        inputSha256="a" * 64, answerSha256=_canonical_hash({"answer": ANSWER}),
        questionSha256=question.question_sha256, asOfDate="2026-10-16",
    )
    payload = {"operationId": str(operation), "draftId": str(draft), "draftRevision": 2,
               "inputSha256": "a" * 64,
               "answerSha256": intent.answer_sha256,
               "questionSha256": question.question_sha256,
               "question": question.model_dump(mode="json", by_alias=True),
               "answer": ANSWER, "confirmedContext": {"intakeVersion": 2},
               "asOfDate": "2026-10-16"}
    payload.update(change or {})
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "transport":
            raise httpx.ReadError("SYNTHETIC_PRIVATE_DETAIL", request=request)
        if failure == "json":
            return httpx.Response(200, text="SYNTHETIC_PRIVATE_DETAIL")
        if failure == "refusal":
            return httpx.Response(409, json={"error": {"code": "SYNTHETIC_PRIVATE_DETAIL",
                                                      "message": "SYNTHETIC_PRIVATE_DETAIL"}})
        return httpx.Response(200, json=payload)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core:8000", "k" * 32), client=http)
            result = await client.get_answer_context(
                operation_id=operation, draft_id=draft, telegram_user_id=123, binding=binding,
            )
            return result, requests, binding, operation
    return run()


def test_private_answer_context_uses_metadata_only_and_checks_whole_binding():
    result, requests, binding, operation = asyncio.run(scenario())
    assert result.answer == ANSWER and len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == f"/v1/intake-answer-preparations/{operation}/context"
    assert json.loads(request.content) == binding.model_dump(mode="json", by_alias=True)
    assert request.headers["x-telegram-user-id"] == "123"
    assert request.headers["x-agent-internal-key"] == "k" * 32
    assert "x-clinic-id" not in request.headers and ANSWER not in request.content.decode()


@pytest.mark.parametrize("change", [
    {"operationId": str(uuid4())}, {"draftId": str(uuid4())}, {"draftRevision": 3},
    {"draftRevision": True}, {"inputSha256": "b" * 64}, {"answerSha256": "b" * 64},
    {"questionSha256": "b" * 64}, {"answer": "x" * 1501}, {"answer": True},
    {"confirmedContext": {"intakeVersion": 2, "attachments": ["SYNTHETIC_PRIVATE_DETAIL"]}},
    {"confirmedContext": {"intakeVersion": True}}, {"leaseToken": str(uuid4())},
])
def test_context_binding_or_shape_mismatch_is_refused_without_private_error_context(change):
    with pytest.raises(LegalCoreProtocolError) as caught:
        asyncio.run(scenario(change))
    assert str(caught.value) == "answer context response contract mismatch"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "SYNTHETIC_PRIVATE_DETAIL" not in repr(caught.value)


@pytest.mark.parametrize("failure", ["transport", "json", "refusal"])
def test_context_failure_strips_body_request_and_exception_chain(failure):
    with pytest.raises((LegalCoreError, LegalCoreProtocolError)) as caught:
        asyncio.run(scenario(failure=failure))
    assert "SYNTHETIC_PRIVATE_DETAIL" not in repr(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    if failure == "refusal":
        assert caught.value.status_code == 409 and caught.value.code == "ANSWER_CONTEXT_REFUSED"
