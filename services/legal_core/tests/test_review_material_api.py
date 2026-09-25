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


def _seed_material() -> tuple[UUID, bytes]:
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
                        title="Проверочный материал",
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

    assert missing_key.status_code == 403
    assert non_editor.status_code == 403
    assert listing.status_code == 200
    item = next(item for item in listing.json()["items"] if item["materialId"] == str(material_id))
    assert item["reviewState"] == "METADATA_REQUIRED"
    assert "rawBytes" not in item
    assert artifact.status_code == 200
    assert artifact.content == raw
    assert artifact.headers["x-legal-artifact-sha256"] == hashlib.sha256(raw).hexdigest()
