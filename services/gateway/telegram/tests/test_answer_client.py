"""Actual HTTP envelope and fail-closed subsequent-answer selectors."""

import asyncio
import json
from copy import deepcopy
from uuid import UUID

import httpx2 as httpx
import pytest
from telegram_gateway.answer_client import AnswerClient
from telegram_gateway.case_wizard import LegalCoreApiError, LegalCoreClient
from telegram_gateway.understanding_client import canonical_hash

OP = UUID("00000000-0000-4000-8000-000000000051")
DRAFT = UUID("00000000-0000-4000-8000-000000000052")


def draft():
    return {"id": str(DRAFT), "revision": 2, "wizardState": "INCOMING",
            "draftData": {"intakeVersion": 2, "eventSummary": "Синтетическая ситуация"}}


def proposed():
    interpretation = {"status": "PROPOSED", "value": "COMPLAINT",
                      "sourceSpans": [{"start": 0, "end": 4}], "reasonCode": None}
    return {"operationId": str(OP), "state": "PROPOSED", "question": {
        "schemaVersion": "intake-answer-question.v1", "wizardState": "INCOMING",
        "targetField": "incomingKind"}, "interpretation": interpretation,
        "source": {"draftId": str(DRAFT), "draftRevision": 2,
                   "inputSha256": canonical_hash({key: draft()[key]
                                                  for key in ("draftData", "wizardState")}),
                   "proposalSha256": canonical_hash(interpretation)}, "acceptance": None}


def test_answer_transport_sends_only_actor_revision_answer_and_receipt_selectors():
    async def scenario():
        calls = []

        async def transport(request):
            calls.append(request)
            if request.url.path.endswith("/acceptance"):
                return httpx.Response(200, json={"operationId": str(OP), "acceptedRevision": 3,
                                                "draft": {**draft(), "revision": 4}})
            return httpx.Response(202, json=proposed())

        async with httpx.AsyncClient(base_url="http://core", transport=httpx.MockTransport(
            transport,
        )) as http:
            client = AnswerClient(LegalCoreClient(http), 777)
            status = await client.start(DRAFT, 2, "Недоволен сервисом")
            await client.proposal_draft(status, draft())
            accepted = await client.accept(status)
        assert calls[0].url.path == f"/v1/telegram-intake-drafts/{DRAFT}/answer-interpretations"
        assert json.loads(calls[0].content) == {
            "expectedRevision": 2, "answer": "Недоволен сервисом"}
        assert calls[0].headers["X-Telegram-User-Id"] == "777"
        assert "X-Agent-Key" not in calls[0].headers
        assert json.loads(calls[1].content) == {
            "expectedRevision": 2, "expectedInputSha256": proposed()["source"]["inputSha256"],
            "expectedProposalSha256": proposed()["source"]["proposalSha256"]}
        assert accepted["revision"] == 4  # replay returns CURRENT draft, not old accepted snapshot
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["private", "missing", "operation", "draft", "hash",
                                      "revision", "question", "outcome", "value"])
def test_malformed_or_stale_public_proposals_are_not_confirmable(mutation):
    async def scenario():
        value = deepcopy(proposed())
        if mutation == "private":
            value["answer"] = "PRIVATE SENTINEL"
        elif mutation == "missing":
            value.pop("acceptance")
        elif mutation == "operation":
            value["operationId"] = str(DRAFT)
        elif mutation == "draft":
            value["source"]["draftId"] = str(OP)
        elif mutation == "hash":
            value["source"]["proposalSha256"] = "0" * 64
        elif mutation == "revision":
            value["source"]["draftRevision"] = True
        elif mutation == "question":
            value["question"]["wizardState"] = "EVENT"
        elif mutation == "value":
            value["interpretation"]["value"] = {"inventedLegalDecision": "ACCEPT"}
        else:
            value["state"] = "UNRESOLVED"

        async with httpx.AsyncClient(base_url="http://core", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=value),
        )) as http:
            with pytest.raises(LegalCoreApiError) as caught:
                client = AnswerClient(LegalCoreClient(http), 777)
                status = await client.get(OP)
                await client.proposal_draft(status, draft())
        assert caught.value.__cause__ is None and caught.value.__context__ is None
        assert "PRIVATE SENTINEL" not in str(caught.value)
    asyncio.run(scenario())


def test_transport_exception_is_sanitized_outside_exception_handler():
    async def scenario():
        def transport(request):
            raise httpx.ReadTimeout("PRIVATE SENTINEL", request=request)
        async with httpx.AsyncClient(base_url="http://core", transport=httpx.MockTransport(
            transport,
        )) as http:
            with pytest.raises(LegalCoreApiError) as caught:
                await AnswerClient(LegalCoreClient(http), 777).start(DRAFT, 2, "PRIVATE SENTINEL")
        assert caught.value.__cause__ is None and caught.value.__context__ is None
        assert "PRIVATE SENTINEL" not in str(caught.value)
    asyncio.run(scenario())
