"""Malformed context transport must not retain an input-bearing exception chain."""

import asyncio
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


@pytest.mark.parametrize("failure", ["malformed_json", "transport"])
def test_context_transport_failures_drop_raw_body_and_credential_exception_context(failure):
    async def invoke():
        def handler(request):
            if failure == "transport":
                raise httpx.ReadError("SYNTHETIC_PRIVATE_SENTINEL", request=request)
            return httpx.Response(200, content=b'{"source":"SYNTHETIC_PRIVATE_SENTINEL"')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await LegalCoreClient(
                LegalCoreEndpoint("http://core:8000", "k" * 32), client=http,
            ).get_understanding_context(
                operation_id=uuid4(), draft_id=uuid4(), telegram_user_id=123,
                binding=UnderstandingContextRequest(
                    leaseToken=uuid4(), expectedRevision=2, expectedInputSha256="a" * 64,
                ),
            )
    with pytest.raises((LegalCoreError, LegalCoreProtocolError)) as error:
        asyncio.run(invoke())
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in str(error.value)
