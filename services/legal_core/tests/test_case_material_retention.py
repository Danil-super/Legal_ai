"""Retention removes raw case material before it permits parent-draft deletion."""

import asyncio
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.case_material_retention import purge_expired_case_materials
from legal_core.clinic_document_store import case_material_object_key
from legal_core.database import database_url, owner_database_url
from legal_core.draft_retention import purge_expired_intake_drafts
from test_case_api import actor_headers, seed_admin

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


class _Store:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    async def delete_case_material(self, *, stored_object_key: str) -> None:
        self.deleted_keys.append(stored_object_key)


def test_expired_material_is_deleted_before_the_expired_draft() -> None:
    from test_case_api import application_client

    actor = 8_300_000_000 + uuid4().int % 100_000_000
    clinic_id, membership_id = seed_admin(actor)
    with application_client() as client:
        created = client.post(
            "/v1/telegram-intake-drafts", json={}, headers=actor_headers(actor, uuid4())
        )
    assert created.status_code == 201, created.text
    draft_id = UUID(created.json()["id"])
    material_id = uuid4()
    object_key = case_material_object_key(
        clinic_id=clinic_id, material_id=material_id, raw_sha256="a" * 64
    )
    owner = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with owner.begin() as connection:
            connection.execute(
                text(
                    "UPDATE telegram_intake_drafts "
                    "SET purge_after=timezone('utc', now()) - interval '1 minute' WHERE id=:id"
                ),
                {"id": draft_id},
            )
            connection.execute(
                text(
                    "INSERT INTO case_materials "
                    "(id, clinic_id, draft_id, uploader_membership_id, display_name, mime_type, "
                    "raw_size_bytes, raw_sha256, raw_object_key, expires_at) "
                    "VALUES (:id, :clinic_id, :draft_id, :membership_id, 'Материал', "
                    "'text/plain', 9, :sha256, :object_key, "
                    "timezone('utc', now()) - interval '1 minute')"
                ),
                {
                    "id": material_id,
                    "clinic_id": clinic_id,
                    "draft_id": draft_id,
                    "membership_id": membership_id,
                    "sha256": "a" * 64,
                    "object_key": object_key,
                },
            )
    finally:
        owner.dispose()

    async def scenario() -> tuple[int, int, int]:
        engine = create_async_engine(database_url())
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            blocked = await purge_expired_intake_drafts(sessions)
            store = _Store()
            removed = await purge_expired_case_materials(sessions, store)
            drafts = await purge_expired_intake_drafts(sessions)
            assert store.deleted_keys == [object_key]
            return blocked, removed, drafts
        finally:
            await engine.dispose()

    assert asyncio.run(scenario()) == (0, 1, 1)
