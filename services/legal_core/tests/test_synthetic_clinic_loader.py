import json
from uuid import UUID

import httpx
import pytest

from legal_core.synthetic_clinic_loader import (
    SyntheticFixtureLoadError,
    _load_documents,
    _validate_target,
    load_synthetic_fixture_pack,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://10.0.0.10:8000",
        "http://legal-core.example.com",
        "http://user:password@localhost:8000",
        "http://localhost:8000/v1",
        "http://localhost:8000?target=prod",
    ],
)
def test_synthetic_loader_rejects_remote_or_ambiguous_targets(url: str) -> None:
    with pytest.raises(SyntheticFixtureLoadError):
        _validate_target(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8000/", "http://127.0.0.1:8000"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://legal-core:8000", "http://legal-core:8000"),
    ],
)
def test_synthetic_loader_accepts_only_local_or_internal_targets(
    url: str,
    expected: str,
) -> None:
    assert _validate_target(url) == expected


def test_synthetic_loader_manifest_is_complete() -> None:
    documents = _load_documents()

    assert len(documents) == 8
    assert {item.document_key for item in documents} == {
        "contract-main",
        "warranty-main",
        "consent-general",
        "consent-implant",
        "patient-rules",
        "medical-record-access",
        "memo-implant",
        "claims-policy",
    }
    assert all(
        item.text.startswith("SYNTHETIC DEVELOPMENT FIXTURE — NOT A LEGAL TEMPLATE")
        for item in documents
    )


def test_synthetic_loader_requires_explicit_write_flag_in_environment() -> None:
    with pytest.raises(SyntheticFixtureLoadError, match="ALLOW_SYNTHETIC_CLINIC_FIXTURES"):
        load_synthetic_fixture_pack(
            telegram_user_id=123,
            base_url="http://localhost:8000",
            environment={},
        )


def test_synthetic_loader_uses_public_api_and_is_replay_safe() -> None:
    documents = _load_documents()
    document_ids = {
        item.document_key: UUID(int=index + 1)
        for index, item in enumerate(documents)
    }
    version_ids = {
        item.document_key: UUID(int=100 + index + 1)
        for index, item in enumerate(documents)
    }
    title_to_key = {item.title: item.document_key for item in documents}
    document_id_to_key = {str(value): key for key, value in document_ids.items()}
    version_id_to_key = {str(value): key for key, value in version_ids.items()}
    seen_calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_calls.append((request.method, request.url.path))
        assert request.headers["X-Telegram-User-Id"] == "123456"
        if request.method == "GET" and request.url.path == "/v1/actor":
            return httpx.Response(200, json={"clinicId": str(UUID(int=999))})

        body = json.loads(request.content.decode()) if request.content else {}
        if request.method == "POST" and request.url.path == "/v1/clinic-documents":
            key = title_to_key[body["title"]]
            assert body["documentKey"] == key
            return httpx.Response(200, json={"id": str(document_ids[key])})

        if request.method == "POST" and request.url.path.endswith("/text-versions"):
            document_id = request.url.path.split("/")[3]
            key = document_id_to_key[document_id]
            fixture = next(item for item in documents if item.document_key == key)
            assert body["normalizedText"] == fixture.text
            assert body["sourceFilename"] == fixture.filename
            return httpx.Response(
                200,
                json={"id": str(version_ids[key]), "versionNo": 1},
            )

        if request.method == "POST" and "/approval-events" in request.url.path:
            version_id = request.url.path.split("/")[4]
            key = version_id_to_key[version_id]
            assert key in document_ids
            assert body == {
                "decision": "APPROVED",
                "reasonCode": "SYNTHETIC_FIXTURE_REVIEW_PASSED",
            }
            return httpx.Response(200, json={"decision": "APPROVED"})

        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url="http://localhost:8000", transport=transport) as client:
        first = load_synthetic_fixture_pack(
            telegram_user_id=123456,
            base_url="http://localhost:8000",
            client=client,
            environment={"ALLOW_SYNTHETIC_CLINIC_FIXTURES": "1"},
        )
        second = load_synthetic_fixture_pack(
            telegram_user_id=123456,
            base_url="http://localhost:8000",
            client=client,
            environment={"ALLOW_SYNTHETIC_CLINIC_FIXTURES": "1"},
        )

    assert first == second
    assert len(first) == 8
    assert all(item.version_no == 1 for item in first)
    assert sum(path == "/v1/actor" for _, path in seen_calls) == 2
    assert len(seen_calls) == 2 * (1 + 8 * 3)


def test_injected_client_cannot_bypass_target_guard() -> None:
    with httpx.Client(base_url="http://legal-core:8000") as client:
        with pytest.raises(SyntheticFixtureLoadError, match="does not match guarded target"):
            load_synthetic_fixture_pack(
                telegram_user_id=123,
                base_url="http://localhost:8000",
                client=client,
                environment={"ALLOW_SYNTHETIC_CLINIC_FIXTURES": "1"},
            )
