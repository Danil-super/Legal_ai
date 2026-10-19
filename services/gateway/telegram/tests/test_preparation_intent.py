"""Synthetic intermediate-origin callbacks and exact Core request bindings."""

import asyncio
import json
from uuid import UUID, uuid4

import httpx2
import pytest
from telegram_gateway import clarification_intent as intent
from telegram_gateway.case_wizard import LegalCoreClient


def test_preparation_callback_is_canonical_compact_and_distinct_from_report():
    case, operation = uuid4(), uuid4()
    value = intent.encode_preparation_callback(case, operation)
    assert len(value.encode()) <= 64
    assert value.startswith("case:clarify2:")
    assert intent.decode_preparation_callback(value) == (case, operation)
    with pytest.raises(ValueError):
        intent.decode_clarification_callback(value)
    with pytest.raises(ValueError):
        intent.decode_preparation_callback(intent.encode_clarification_callback(case, operation))


@pytest.mark.parametrize("token", ["", "A" * 42, "A" * 44, "A" * 42 + "=",
                                   "A" * 42 + "+", "A" * 42 + "B", "ф" * 43])
def test_preparation_callback_rejects_malformed_and_noncanonical_payloads(token):
    with pytest.raises(ValueError):
        intent.decode_preparation_callback("case:clarify2:" + token)


def test_client_forwards_only_exact_operation_origin_and_stable_actor_key():
    case, operation, key = uuid4(), uuid4(), uuid4()
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(200, json={})

    async def run():
        async with httpx2.AsyncClient(base_url="http://core.test",
                                     transport=httpx2.MockTransport(handle)) as http:
            client = LegalCoreClient(http)
            await client.clarification_preview(case, 7_000_000_001,
                                                source_operation_id=operation)
            await client.create_clarification_draft(
                case, 7_000_000_001, expected_sha256="a" * 64, key=key,
                expected_operation_id=operation, expected_operation_sha256="b" * 64,
            )

    asyncio.run(run())
    assert str(requests[0].url.path) == f"/v1/cases/{case}/clarification-drafts/preview"
    assert dict(requests[0].url.params) == {"operation_id": str(operation)}
    assert requests[0].headers["X-Telegram-User-Id"] == "7000000001"
    assert requests[1].headers["Idempotency-Key"] == str(key)
    assert json.loads(requests[1].content) == {
        "expectedFactSnapshotSha256": "a" * 64,
        "expectedOperationId": str(operation), "expectedOperationSha256": "b" * 64,
    }


@pytest.mark.parametrize("binding", [
    {"expected_operation_id": uuid4()}, {"expected_operation_sha256": "b" * 64},
    {"expected_operation_id": "not-a-uuid", "expected_operation_sha256": "b" * 64},
    {"expected_operation_id": uuid4(), "expected_operation_sha256": "B" * 64},
    {"expected_operation_id": uuid4(), "expected_operation_sha256": "b" * 63},
    {"expected_operation_id": uuid4(), "expected_operation_sha256": "b" * 64,
     "expected_report_id": uuid4(), "expected_report_sha256": "c" * 64},
])
def test_client_refuses_half_malformed_or_mixed_operation_bindings_before_http(binding):
    def unexpected(_request):
        pytest.fail("invalid origin must not make an HTTP request")

    async def run():
        async with httpx2.AsyncClient(base_url="http://core.test",
                                     transport=httpx2.MockTransport(unexpected)) as http:
            with pytest.raises(ValueError):
                await LegalCoreClient(http).create_clarification_draft(
                    uuid4(), 7_000_000_001, expected_sha256="a" * 64, key=uuid4(), **binding,
                )

    asyncio.run(run())


def test_client_refuses_two_source_selectors_before_http():
    async def run():
        async with httpx2.AsyncClient(base_url="http://core.test") as http:
            with pytest.raises(ValueError):
                await LegalCoreClient(http).clarification_preview(
                    UUID(int=1), 7_000_000_001, source_report_id=UUID(int=2),
                    source_operation_id=UUID(int=3),
                )

    asyncio.run(run())
