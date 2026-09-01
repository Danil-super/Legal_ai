from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
import pytest

from legal_core.clinic_document_store import (
    MinioRawClinicDocumentStore,
    MinioSettings,
    clinic_document_object_key,
)
from legal_core.clinic_document_parser import TEXT_MIME, sha256_bytes

CLINIC_ID = UUID("00000000-0000-0000-0000-000000000101")
DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000102")


def _settings() -> MinioSettings:
    return MinioSettings(
        host="minio",
        port=9000,
        access_key="clinic-app",
        secret_key="secret-key-for-tests",
        bucket="clinic-documents",
    )


def test_object_key_is_content_addressed_and_rejects_invalid_hash() -> None:
    digest = "a" * 64
    assert clinic_document_object_key(
        clinic_id=CLINIC_ID,
        document_id=DOCUMENT_ID,
        raw_sha256=digest,
    ) == f"clinic/{CLINIC_ID}/{DOCUMENT_ID}/{digest}"

    with pytest.raises(ValueError, match="SHA-256"):
        clinic_document_object_key(
            clinic_id=CLINIC_ID,
            document_id=DOCUMENT_ID,
            raw_sha256="not-a-hash",
        )


def test_store_creates_bucket_once_and_signs_each_request() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "HEAD" and request.url.path == "/clinic-documents":
                return httpx.Response(404, request=request)
            if request.method == "PUT" and request.url.path == "/clinic-documents":
                return httpx.Response(200, request=request)
            if request.method == "PUT" and request.url.path.startswith(
                "/clinic-documents/clinic/"
            ):
                return httpx.Response(200, request=request)
            return httpx.Response(500, request=request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        ) as client:
            store = MinioRawClinicDocumentStore(_settings(), client=client)
            first = b"first clinic document"
            first_sha = sha256_bytes(first)
            first_key = await store.put(
                clinic_id=CLINIC_ID,
                document_id=DOCUMENT_ID,
                raw_sha256=first_sha,
                content=first,
                content_type=TEXT_MIME,
            )
            second = b"second clinic document"
            second_sha = sha256_bytes(second)
            second_key = await store.put(
                clinic_id=CLINIC_ID,
                document_id=DOCUMENT_ID,
                raw_sha256=second_sha,
                content=second,
                content_type=TEXT_MIME,
            )

        assert first_key.endswith(first_sha)
        assert second_key.endswith(second_sha)
        assert [request.method for request in requests] == ["HEAD", "PUT", "PUT", "PUT"]
        assert sum(request.url.path == "/clinic-documents" for request in requests) == 2
        object_requests = [
            request for request in requests if request.url.path.startswith("/clinic-documents/clinic/")
        ]
        assert len(object_requests) == 2
        for request in requests:
            authorization = request.headers["authorization"]
            assert authorization.startswith("AWS4-HMAC-SHA256 Credential=clinic-app/")
            assert "secret-key-for-tests" not in authorization
            assert request.headers["x-amz-date"]
            assert request.headers["x-amz-content-sha256"]
        assert object_requests[0].headers["x-amz-content-sha256"] == first_sha
        assert object_requests[0].content == first

    asyncio.run(scenario())


def test_store_rejects_hash_mismatch_before_http_request() -> None:
    async def scenario() -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            store = MinioRawClinicDocumentStore(_settings(), client=client)
            with pytest.raises(ValueError, match="does not match"):
                await store.put(
                    clinic_id=CLINIC_ID,
                    document_id=DOCUMENT_ID,
                    raw_sha256="0" * 64,
                    content=b"actual",
                    content_type=TEXT_MIME,
                )
        assert calls == 0

    asyncio.run(scenario())


def test_store_rejects_redirect_instead_of_following_it() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                307,
                headers={"Location": "http://attacker.invalid/steal"},
                request=request,
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        ) as client:
            store = MinioRawClinicDocumentStore(_settings(), client=client)
            content = b"document"
            with pytest.raises(RuntimeError, match="unexpected redirect"):
                await store.put(
                    clinic_id=CLINIC_ID,
                    document_id=DOCUMENT_ID,
                    raw_sha256=sha256_bytes(content),
                    content=content,
                    content_type=TEXT_MIME,
                )

    asyncio.run(scenario())
