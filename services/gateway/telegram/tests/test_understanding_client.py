"""Actor-only wire requests and strict durable proposal binding."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.understanding_client import UnderstandingClient, canonical_hash

OP = UUID("00000000-0000-4000-8000-000000000019")
DRAFT = UUID("00000000-0000-4000-8000-000000000029")


def draft():
    return {"id": str(DRAFT), "revision": 2, "wizardState": "INCOMING",
            "draftData": {"intakeVersion": 2, "eventSummary": "Описание синтетической ситуации"}}


def proposed():
    fields = {"eventDate": {"date": None, "precision": "UNKNOWN"},
              "sourceSpans": {"eventDate": [{"start": 0, "end": 4}]}}
    return {"operationId": str(OP), "state": "PROPOSED", "suggestions": fields,
            "source": {"draftId": str(DRAFT), "draftRevision": 2,
                       "inputSha256": canonical_hash({key: draft()[key]
                                                      for key in ("draftData", "wizardState")}),
                       "proposalSha256": canonical_hash(fields)}, "acceptance": None}


def test_start_and_accept_forward_only_owned_metadata_and_exact_proposal_hashes():
    async def scenario():
        request = AsyncMock(return_value=proposed())
        core = SimpleNamespace(_json_request=request)
        client = UnderstandingClient(core, 777)
        status = await client.start(DRAFT, 2)
        request.assert_awaited_once_with(
            "POST", f"/v1/telegram-intake-drafts/{DRAFT}/understanding",
            telegram_user_id=777, payload={"expectedRevision": 2},
        )
        await client.proposal_draft(status, draft())
        result = {"operationId": str(OP), "acceptedRevision": 3,
                  "draft": {**draft(), "revision": 3}}
        request.return_value = result
        accepted = await client.accept(status)
        assert accepted["revision"] == 3
        assert request.await_args.kwargs["payload"] == {
            "expectedRevision": 2, "expectedInputSha256": proposed()["source"]["inputSha256"],
            "expectedProposalSha256": proposed()["source"]["proposalSha256"],
        }
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", [
    "operation", "private", "source", "proposal", "bool_revision", "missing_field",
])
def test_bad_receipts_or_bindings_never_become_confirmable(mutation):
    async def scenario():
        payload = deepcopy(proposed())
        if mutation == "operation":
            payload["operationId"] = str(DRAFT)
        elif mutation == "private":
            payload["leaseToken"] = str(DRAFT)
        elif mutation == "source":
            payload["source"]["inputSha256"] = "0" * 64
        elif mutation == "proposal":
            payload["source"]["proposalSha256"] = "0" * 64
        elif mutation == "bool_revision":
            payload["source"]["draftRevision"] = True
        else:
            payload.pop("acceptance")
        core = SimpleNamespace(_json_request=AsyncMock(return_value=payload))
        client = UnderstandingClient(core, 777)
        with pytest.raises(LegalCoreApiError) as caught:
            status = await client.get(OP)
            await client.proposal_draft(status, draft())
        assert caught.value.__cause__ is None and caught.value.__context__ is None
        assert "Описание" not in str(caught.value)
    asyncio.run(scenario())
