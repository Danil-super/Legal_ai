import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from legal_core.corpus_loader import (
    CorpusFragment,
    corpus_fragments_sha256,
    ingest_manifest,
    normalized_text_sha256,
)
from legal_core.database import database_url, owner_database_url
from legal_core.main import create_app
from legal_core.models import LegalApprovalEvent, LegalVersion
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run legal editor workspace API tests",
)

_GATEWAY_KEY = "editor-workspace-test-key-000000000001"


def _client() -> TestClient:
    engine = create_async_engine(database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return TestClient(
        create_app(
            session_factory=factory,
            managed_engine=engine,
            enable_draft_retention=False,
        )
    )


def _seed_user(telegram_user_id: int, *, system_role: str | None = None) -> UUID:
    user_id = uuid4()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id,telegram_user_id,display_name,system_role) "
                    "VALUES (:id,:telegram_user_id,'Editor workspace API test',:system_role)"
                ),
                {
                    "id": user_id,
                    "telegram_user_id": telegram_user_id,
                    "system_role": system_role,
                },
            )
    finally:
        engine.dispose()
    return user_id


def _write_manifest(
    tmp_path: Path,
    *,
    source_key: str | None = None,
    document_key: str | None = None,
    fragment_text: str = "Проверочный фрагмент официального документа.",
    effective_from: str = "2026-02-01",
    effective_to: str = "2027-02-01",
) -> Path:
    raw = b"%PDF-1.7\nlegal-editor-workspace-api-test\n%%EOF\n"
    artifact_name = f"official-{uuid4().hex}.pdf"
    (tmp_path / artifact_name).write_bytes(raw)
    normalized_text = f"Заголовок. {fragment_text} Конец документа."
    fragment = CorpusFragment(
        ordinal=1,
        article=None,
        part=None,
        point="1",
        heading=None,
        structural_path="point:1",
        text=fragment_text,
    )
    manifest = tmp_path / f"manifest-{uuid4().hex}.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": "dental-legal-corpus.v2",
                "source_key": source_key or f"editor-api-source-{uuid4().hex}",
                "source_name": "Editor API official test source",
                "source_url": "https://example.gov.ru/legal-editor-api-test",
                "source_external_id": "editor-api-test",
                "allowed_hosts": ["example.gov.ru"],
                "document_key": document_key or f"editor-api-document-{uuid4().hex}",
                "document_type": "DECREE",
                "title": "Editor API official legal document",
                "issuer": "Editor API test authority",
                "official_number": "editor-api-test",
                "adoption_date": "2026-01-01",
                "publication_date": "2026-01-02",
                "version_date": "2026-01-01",
                "effective_from": effective_from,
                "effective_to": effective_to,
                "approval_state": "REVIEW_REQUIRED",
                "artifact_kind": "OFFICIAL_RAW",
                "artifact_mime_type": "application/pdf",
                "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                "artifact_path": artifact_name,
                "artifact_retrieved_at": "2026-08-22T00:00:00Z",
                "artifact_size_bytes": len(raw),
                "artifact_page_count": 1,
                "normalized_text": normalized_text,
                "normalized_sha256": normalized_text_sha256(normalized_text),
                "fragments_sha256": corpus_fragments_sha256([fragment]),
                "normalization_scope": "FULL_DOCUMENT",
                "parser_version": "editor-api-test.v1",
                "fragments": [fragment.model_dump(mode="json")],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest


def _ingest(manifest: Path) -> UUID:
    async def scenario() -> UUID:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await ingest_manifest(factory, manifest)
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def _version_payload(version_id: UUID) -> dict[str, object]:
    async def scenario() -> dict[str, object]:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                version = await session.get(LegalVersion, version_id)
                assert version is not None
                return {
                    "expectedSha256": version.raw_sha256,
                    "expectedNormalizedSha256": version.normalized_sha256,
                    "expectedFragmentsSha256": version.fragments_sha256,
                    "expectedEffectiveFrom": version.effective_from.isoformat(),
                    "expectedEffectiveTo": (
                        None if version.effective_to is None else version.effective_to.isoformat()
                    ),
                    "sourceIsOfficial": True,
                    "officialTextCompared": True,
                    "artifactIsComplete": True,
                    "effectiveDatesVerified": True,
                    "fragmentsVerified": True,
                }
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def _approval_event_count(version_id: UUID) -> int:
    async def scenario() -> int:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                return int(
                    await session.scalar(
                        select(func.count(LegalApprovalEvent.id)).where(
                            LegalApprovalEvent.legal_version_id == version_id
                        )
                    )
                    or 0
                )
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def _headers(
    telegram_user_id: int, *, key: str | None, idempotency_key: UUID | None = None
) -> dict[str, str]:
    headers = {"X-Telegram-User-Id": str(telegram_user_id)}
    if key is not None:
        headers["X-Legal-Editor-Gateway-Key"] = key
    if idempotency_key is not None:
        headers["Idempotency-Key"] = str(idempotency_key)
    return headers


def test_editor_workspace_enforces_gateway_authz_and_replays_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", _GATEWAY_KEY)
    editor_telegram_id = 8_310_000_000 + uuid4().int % 100_000_000
    non_editor_telegram_id = 8_410_000_000 + uuid4().int % 100_000_000
    _seed_user(editor_telegram_id, system_role="LEGAL_EDITOR")
    _seed_user(non_editor_telegram_id)
    version_id = _ingest(_write_manifest(tmp_path))
    request = _version_payload(version_id)
    idempotency_key = uuid4()

    with _client() as client:
        missing_key = client.get(
            "/v1/legal/editor/status",
            headers=_headers(editor_telegram_id, key=None),
        )
        wrong_key = client.get(
            "/v1/legal/editor/status",
            headers=_headers(editor_telegram_id, key="z" * 32),
        )
        non_editor = client.get(
            "/v1/legal/editor/status",
            headers=_headers(non_editor_telegram_id, key=_GATEWAY_KEY),
        )
        editor = client.get(
            "/v1/legal/editor/status",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY),
        )
        queue = client.get(
            "/v1/legal/review-queue?page=1",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY),
        )
        detail = client.get(
            f"/v1/legal/review-queue/{version_id}",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY),
        )
        artifact = client.get(
            f"/v1/legal/review-queue/{version_id}/artifact",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY),
        )
        fragments = client.get(
            f"/v1/legal/review-queue/{version_id}/fragments?page=1",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY),
        )

        stale_request = dict(request)
        stale_request["expectedSha256"] = "f" * 64
        stale = client.post(
            f"/v1/legal/review-queue/{version_id}/approval-events",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY, idempotency_key=uuid4()),
            json=stale_request,
        )
        approved = client.post(
            f"/v1/legal/review-queue/{version_id}/approval-events",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY, idempotency_key=idempotency_key),
            json=request,
        )
        replayed = client.post(
            f"/v1/legal/review-queue/{version_id}/approval-events",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY, idempotency_key=idempotency_key),
            json=request,
        )
        changed_replay = dict(request)
        changed_replay["expectedSha256"] = "e" * 64
        conflicting_replay = client.post(
            f"/v1/legal/review-queue/{version_id}/approval-events",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY, idempotency_key=idempotency_key),
            json=changed_replay,
        )

    assert missing_key.status_code == 403
    assert wrong_key.status_code == 403
    assert non_editor.status_code == 403
    assert editor.json() == {"isLegalEditor": True}
    assert queue.status_code == 200
    assert any(item["versionId"] == str(version_id) for item in queue.json()["items"])
    assert detail.json()["approvalEligible"] is True
    assert artifact.status_code == 200
    assert artifact.headers["content-type"] == "application/pdf"
    assert artifact.headers["x-legal-artifact-sha256"] == request["expectedSha256"]
    assert fragments.json()["items"][0]["fragmentText"].startswith("Проверочный фрагмент")
    assert stale.status_code == 409
    assert _approval_event_count(version_id) == 1
    assert approved.status_code == 200
    assert replayed.status_code == 200
    assert approved.json() == replayed.json()
    assert conflicting_replay.status_code == 422


def test_editor_queue_shows_only_the_latest_unexpired_revision_of_each_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", _GATEWAY_KEY)
    editor_telegram_id = 8_510_000_000 + uuid4().int % 100_000_000
    _seed_user(editor_telegram_id, system_role="LEGAL_EDITOR")

    with _client() as client:
        baseline = client.get(
            "/v1/legal/review-queue?page=1",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY),
        )

    assert baseline.status_code == 200
    baseline_total = baseline.json()["totalItems"]

    source_key = f"editor-api-source-{uuid4().hex}"
    document_key = f"editor-api-document-{uuid4().hex}"
    first_revision = _ingest(
        _write_manifest(
            tmp_path,
            source_key=source_key,
            document_key=document_key,
            fragment_text="Первый отобранный фрагмент.",
            effective_to="2999-02-01",
        )
    )
    latest_revision = _ingest(
        _write_manifest(
            tmp_path,
            source_key=source_key,
            document_key=document_key,
            fragment_text="Расширенный отобранный фрагмент.",
            effective_to="2999-02-01",
        )
    )
    expired_document = _ingest(
        _write_manifest(
            tmp_path,
            fragment_text="Фрагмент утратившего силу документа.",
            effective_from="2000-02-01",
            effective_to="2001-02-01",
        )
    )
    superseded_document_key = f"editor-api-document-{uuid4().hex}"
    prior_revision = _ingest(
        _write_manifest(
            tmp_path,
            document_key=superseded_document_key,
            fragment_text="Прежняя ревизия, не подлежащая возврату в очередь.",
            effective_to="2999-02-01",
        )
    )
    expired_latest_revision = _ingest(
        _write_manifest(
            tmp_path,
            document_key=superseded_document_key,
            fragment_text="Последняя, но уже истекшая ревизия.",
            effective_from="2000-02-01",
            effective_to="2001-02-01",
        )
    )

    with _client() as client:
        queue = client.get(
            "/v1/legal/review-queue?page=1",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY),
        )

    assert queue.status_code == 200
    payload = queue.json()
    shown_version_ids = {item["versionId"] for item in payload["items"]}
    assert payload["totalItems"] == baseline_total + 1
    assert str(latest_revision) in shown_version_ids
    assert str(first_revision) not in shown_version_ids
    assert str(expired_document) not in shown_version_ids
    assert str(prior_revision) not in shown_version_ids
    assert str(expired_latest_revision) not in shown_version_ids
