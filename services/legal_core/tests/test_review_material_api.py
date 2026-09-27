import asyncio
import hashlib
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from legal_core.database import database_url, owner_database_url
from legal_core.main import create_app
from legal_core.models import LegalReviewMaterial
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run legal review material API tests",
)

_GATEWAY_KEY = "review-material-api-test-key-00000001"


def _prepare_material(material_id: UUID, raw: bytes, **fields):
    from legal_core.material_preparation import MaterialPreparationInput, store_preparation

    async def scenario():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                result = await store_preparation(session, material_id,
                    MaterialPreparationInput.model_validate({
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                        "title": "Prepared synthetic document", "kind": "NORMATIVE",
                        "group_key": "general", "parser_version": "test-v1",
                        "extraction_scope": "NONE", **fields,
                    }))
                return result.id
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def test_preparation_card_is_editor_only_and_exposes_no_extracted_text(monkeypatch):
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", _GATEWAY_KEY)
    actor = 8_950_000_000 + uuid4().int % 10_000_000
    _seed_user(actor, system_role="LEGAL_EDITOR")
    material_id, raw = _seed_material("Original title")
    headers = _headers(actor, key=_GATEWAY_KEY)
    endpoint = f"/v1/legal/review-materials/{material_id}/preparation"
    with _client() as client:
        assert client.get(endpoint, headers=headers).status_code == 404
        prepared_id = _prepare_material(material_id, raw)
        assert client.get(endpoint, headers=_headers(actor, key=None)).status_code == 403
        response = client.get(endpoint, headers=headers)
        assert response.status_code == 200
        card = response.json()
        assert card["preparationId"] == str(prepared_id)
        assert card["title"] == "Prepared synthetic document"
        assert card["referenceYear"] is None
        assert "normalizedText" not in card and "rawBytes" not in card
        assert "intended_parts" in card["missingFields"]
        group = client.get("/v1/legal/editor/groups/general", headers=headers).json()
        items = group["items"]
        for page in range(2, (group["totalItems"] + 9) // 10 + 1):
            items.extend(client.get(f"/v1/legal/editor/groups/general?page={page}",
                                    headers=headers).json()["items"])
        item = next(item for item in items if item["materialId"] == str(material_id))
        assert item["title"] == card["title"]
        assert item["preparationId"] == str(prepared_id)


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


def _seed_user(telegram_user_id: int, *, system_role: str | None = None) -> None:
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id,telegram_user_id,display_name,system_role) "
                    "VALUES (:id,:telegram_user_id,'Review material API test',:system_role)"
                ),
                {
                    "id": uuid4(),
                    "telegram_user_id": telegram_user_id,
                    "system_role": system_role,
                },
            )
    finally:
        engine.dispose()


def _seed_material(title: str = "Проверочный материал") -> tuple[UUID, bytes]:
    raw = b"{\\rtf1\\ansi review material API test}"
    material_id = uuid4()

    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory.begin() as session:
                session.add(
                    LegalReviewMaterial(
                        id=material_id,
                        package_key=f"api-test-{uuid4().hex}",
                        original_filename="review.rtf",
                        title=title,
                        kind="LEGAL_COPY",
                        review_state="METADATA_REQUIRED",
                        source_name="Гарант",
                        source_url="https://internet.garant.ru/document/redirect/12191967/0",
                        source_external_id="12191967",
                        raw_mime_type="application/rtf",
                        raw_sha256=hashlib.sha256(raw).hexdigest(),
                        raw_bytes=raw,
                        raw_size_bytes=len(raw),
                        received_at=datetime(2026, 9, 25, tzinfo=UTC),
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    return material_id, raw


def _headers(telegram_user_id: int, *, key: str | None) -> dict[str, str]:
    headers = {"X-Telegram-User-Id": str(telegram_user_id)}
    if key is not None:
        headers["X-Legal-Editor-Gateway-Key"] = key
    return headers


def test_review_materials_are_editor_only_and_artifacts_are_integrity_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", _GATEWAY_KEY)
    editor_telegram_id = 8_510_000_000 + uuid4().int % 100_000_000
    non_editor_telegram_id = 8_610_000_000 + uuid4().int % 100_000_000
    _seed_user(editor_telegram_id, system_role="LEGAL_EDITOR")
    _seed_user(non_editor_telegram_id)
    material_id, raw = _seed_material()

    with _client() as client:
        missing_key = client.get(
            "/v1/legal/review-materials?page=1",
            headers=_headers(editor_telegram_id, key=None),
        )
        non_editor = client.get(
            "/v1/legal/review-materials?page=1",
            headers=_headers(non_editor_telegram_id, key=_GATEWAY_KEY),
        )
        listing = client.get(
            "/v1/legal/review-materials?page=1",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY),
        )
        artifact = client.get(
            f"/v1/legal/review-materials/{material_id}/artifact",
            headers=_headers(editor_telegram_id, key=_GATEWAY_KEY),
        )
        assert listing.status_code == 200
        items = listing.json()["items"]
        # Receipt timestamps are fixed in fixtures; the target need not be on page 1.
        for page in range(2, (listing.json()["totalItems"] + 9) // 10 + 1):
            page_response = client.get(
                f"/v1/legal/review-materials?page={page}",
                headers=_headers(editor_telegram_id, key=_GATEWAY_KEY),
            )
            assert page_response.status_code == 200
            items.extend(page_response.json()["items"])

    assert missing_key.status_code == 403
    assert non_editor.status_code == 403
    assert listing.status_code == 200
    item = next(item for item in items if item["materialId"] == str(material_id))
    assert item["reviewState"] == "METADATA_REQUIRED"
    assert "rawBytes" not in item
    assert artifact.status_code == 200
    assert artifact.content == raw
    assert artifact.headers["x-legal-artifact-sha256"] == hashlib.sha256(raw).hexdigest()


def test_material_groups_filter_without_loading_artifact_bytes(monkeypatch) -> None:
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", _GATEWAY_KEY)
    editor_id = 8_710_000_000 + uuid4().int % 100_000_000
    _seed_user(editor_id, system_role="LEGAL_EDITOR")
    labour_id, _ = _seed_material("Трудовой кодекс Российской Федерации")
    other_id, _ = _seed_material("Обзор практики рассмотрения судами дел по спорам")
    headers = _headers(editor_id, key=_GATEWAY_KEY)
    with _client() as client:
        response = client.get("/v1/legal/review-materials?group=labour", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["selectedGroup"] == "labour"
        assert str(labour_id) in {item["materialId"] for item in body["items"]}
        assert str(other_id) not in {item["materialId"] for item in body["items"]}
        assert all(item["groupKey"] == "labour" for item in body["items"])
        assert any(item["key"] == "courts" for item in body["groups"])
        courts = client.get("/v1/legal/review-materials?group=courts", headers=headers).json()
        court_ids = {item["materialId"] for item in courts["items"]}
        for page in range(2, (courts["totalItems"] + 9) // 10 + 1):
            more = client.get(
                f"/v1/legal/review-materials?group=courts&page={page}", headers=headers
            ).json()
            court_ids.update(item["materialId"] for item in more["items"])
        assert str(other_id) in court_ids
        invalid = client.get("/v1/legal/review-materials?group=unknown", headers=headers)
        assert invalid.status_code == 422
        assert client.get("/v1/legal/review-materials?group=labour").status_code in {403, 422}
