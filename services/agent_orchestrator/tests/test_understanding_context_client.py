"""Private context is acquired from Core, never supplied as arbitrary source text."""

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
from legal_core.intake_preparation_contracts import UnderstandingContextRequest


def _scenario(wire_change=None, status=200):
    operation, draft, token = uuid4(), uuid4(), uuid4()
    binding = UnderstandingContextRequest(
        leaseToken=token, expectedRevision=2, expectedInputSha256="a" * 64,
    )
    wire = {
        "operationId": str(operation), "draftId": str(draft), "draftRevision": 2,
        "inputSha256": "a" * 64, "operationKind": "UNDERSTAND_INTAKE",
        "source": "Описание вымышленной ситуации для проверки.",
    }
    if wire_change:
        wire.update(wire_change)
    if status != 200:
        wire = {"error": {"code": "PREPARATION_LEASE_EXPIRED", "message": "Lease expired"}}
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=wire)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            core = LegalCoreClient(LegalCoreEndpoint("http://core:8000", "k" * 32), client=http)
            result = await core.get_understanding_context(
                operation_id=operation, draft_id=draft, telegram_user_id=123, binding=binding,
            )
            return result, requests, binding, operation, draft
    return run()


def test_client_sends_only_private_metadata_and_checks_all_source_bindings():
    result, requests, binding, operation, draft = asyncio.run(_scenario())
    assert result.operation_id == operation and result.draft_id == draft
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == f"/v1/intake-preparations/{operation}/context"
    assert json.loads(request.content) == binding.model_dump(mode="json", by_alias=True)
    assert request.headers["x-telegram-user-id"] == "123"
    assert request.headers["x-agent-internal-key"] == "k" * 32
    assert "x-clinic-id" not in request.headers


@pytest.mark.parametrize("change", [
    {"operationId": str(uuid4())}, {"draftId": str(uuid4())}, {"draftRevision": 3},
    {"draftRevision": True}, {"inputSha256": "b" * 64},
    {"operationKind": "INTERPRET_ANSWER"}, {"source": "short"}, {"source": "x" * 1501},
    {"source": 12345}, {"confirmed": True}, {"leaseToken": str(uuid4())},
])
def test_client_rejects_wrong_binding_or_unexpected_context_without_error_input(change):
    with pytest.raises(LegalCoreProtocolError) as error:
        asyncio.run(_scenario(change))
    assert error.value.__context__ is None
    assert "x" * 100 not in str(error.value)


def test_core_refusal_is_not_relabelled_as_a_model_suggestion():
    with pytest.raises(LegalCoreError) as error:
        asyncio.run(_scenario(status=409))
    assert error.value.status_code == 409 and error.value.code == "PREPARATION_LEASE_EXPIRED"
