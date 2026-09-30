"""Expiry removes a private evaluation source before clearing its scenario content."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.clinic_document_store import reference_evaluation_object_key
from legal_core.database import database_url, owner_database_url
from legal_core.reference_evaluation_retention import purge_expired_reference_evaluations
from test_case_api import seed_admin
from test_reference_evaluation_api import FakeReferenceEvaluationStore, _grant

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


def test_expired_evaluation_version_deletes_private_object_before_clearing_content() -> None:
    actor = 8_950_000_000 + uuid4().int % 100_000_000
    clinic_id, membership_id = seed_admin(actor, role="CLINIC_OWNER")
    _grant(clinic_id, membership_id, "CONTRIBUTOR")
    case_id = uuid4()
    version_id = uuid4()
    raw = b"Synthetic de-identified reference case file."
    raw_sha256 = "a" * 64
    object_key = reference_evaluation_object_key(
        clinic_id=clinic_id,
        version_id=version_id,
        raw_sha256=raw_sha256,
    )
    owner = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with owner.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO reference_evaluation_cases "
                    "(id,clinic_id,created_by_membership_id,group_key,status,current_version) "
                    "VALUES (:id,:clinic_id,:membership_id,'healthcare','DRAFT',1)"
                ),
                {"id": case_id, "clinic_id": clinic_id, "membership_id": membership_id},
            )
            connection.execute(
                text(
                    "INSERT INTO reference_evaluation_case_versions "
                    "(id,clinic_id,reference_case_id,version,created_by_membership_id,as_of_date,"
                    "expected_route,scenario_text,scenario_sha256,raw_mime_type,raw_size_bytes,"
                    "raw_sha256,raw_object_key,content_expires_at) VALUES "
                    "(:id,:clinic_id,:case_id,1,:membership_id,'2026-06-01','ABSTAIN',"
                    "'Synthetic de-identified factual scenario. ',:scenario_sha256,'text/plain',"
                    ":raw_size,:raw_sha256,:object_key,timezone('utc', now()) - "
                    "interval '1 minute')"
                ),
                {
                    "id": version_id,
                    "clinic_id": clinic_id,
                    "case_id": case_id,
                    "membership_id": membership_id,
                    "scenario_sha256": "b" * 64,
                    "raw_size": len(raw),
                    "raw_sha256": raw_sha256,
                    "object_key": object_key,
                },
            )
    finally:
        owner.dispose()
    store = FakeReferenceEvaluationStore()
    store.objects[object_key] = (raw, "text/plain")

    async def scenario() -> int:
        engine = create_async_engine(database_url())
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await purge_expired_reference_evaluations(sessions, store)
        finally:
            await engine.dispose()

    assert asyncio.run(scenario()) == 1
    assert store.deleted == [object_key]
    verify = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with verify.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT scenario_text,raw_object_key,raw_sha256,content_purged_at "
                    "FROM reference_evaluation_case_versions WHERE id=:id"
                ),
                {"id": version_id},
            ).mappings().one()
    finally:
        verify.dispose()
    assert row["scenario_text"] is None
    assert row["raw_object_key"] is None
    assert row["raw_sha256"] is None
    assert row["content_purged_at"] is not None
