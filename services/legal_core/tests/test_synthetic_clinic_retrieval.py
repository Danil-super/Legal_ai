import asyncio
import os
from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from legal_core.clinic_document_retrieval import (
    ApprovedClinicDocumentContextRepository,
    clinic_document_context_trace_sha256,
)
from legal_core.database import database_url, owner_database_url
from legal_core.main import create_app
from legal_core.synthetic_clinic_fixtures import load_synthetic_clinic_versions
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run synthetic clinic retrieval integration tests",
)


def _seed_admin(telegram_user_id: int) -> UUID:
    clinic_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO clinics (id,name) VALUES (:id,'Synthetic fixture clinic')"),
                {"id": clinic_id},
            )
            connection.execute(
                text("INSERT INTO users (id,telegram_user_id) VALUES (:id,:telegram_id)"),
                {"id": user_id, "telegram_id": telegram_user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO clinic_users (id,clinic_id,user_id,role) "
                    "VALUES (:id,:clinic_id,:user_id,'CLINIC_ADMIN')"
                ),
                {"id": membership_id, "clinic_id": clinic_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO subscription_entitlements "
                    "(clinic_id,user_id,status,plan_code,starts_at) VALUES "
                    "(:clinic_id,:user_id,'ACTIVE','MVP',"
                    "timezone('utc', now())-INTERVAL '1 day')"
                ),
                {"clinic_id": clinic_id, "user_id": user_id},
            )
    finally:
        engine.dispose()
    return clinic_id


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


def _headers(telegram_user_id: int) -> dict[str, str]:
    return {"X-Telegram-User-Id": str(telegram_user_id)}


def _install_fixture_pack(
    api: TestClient,
    telegram_user_id: int,
) -> dict[tuple[str, int], str]:
    document_ids: dict[str, str] = {}
    version_ids: dict[tuple[str, int], str] = {}
    for fixture in load_synthetic_clinic_versions():
        document_id = document_ids.get(fixture.document_key)
        if document_id is None:
            created = api.post(
                "/v1/clinic-documents",
                headers=_headers(telegram_user_id),
                json={
                    "documentKey": fixture.document_key,
                    "documentType": fixture.document_type,
                    "title": fixture.title,
                },
            )
            assert created.status_code in {200, 201}
            document_id = created.json()["id"]
            document_ids[fixture.document_key] = document_id

        version = api.post(
            f"/v1/clinic-documents/{document_id}/text-versions",
            headers=_headers(telegram_user_id),
            json={
                "sourceFilename": fixture.filename,
                "normalizedText": fixture.text,
                "validFrom": (
                    None if fixture.valid_from is None else fixture.valid_from.isoformat()
                ),
                "validTo": None if fixture.valid_to is None else fixture.valid_to.isoformat(),
            },
        )
        assert version.status_code in {200, 201}
        assert version.json()["rawSha256"] == fixture.normalized_text_sha256
        assert version.json()["normalizedTextSha256"] == fixture.normalized_text_sha256
        assert version.json()["versionNo"] == fixture.version_no
        version_ids[(fixture.document_key, fixture.version_no)] = version.json()["id"]

        approved = api.post(
            f"/v1/clinic-documents/versions/{version.json()['id']}/approval-events",
            headers=_headers(telegram_user_id),
            json={
                "decision": "APPROVED",
                "reasonCode": "SYNTHETIC_FIXTURE_REVIEW_PASSED",
            },
        )
        assert approved.status_code == 201
    return version_ids


def _search(
    api: TestClient,
    telegram_user_id: int,
    query: str,
    as_of_date: str,
) -> list[dict[str, object]]:
    response = api.get(
        "/v1/clinic-documents/fragments",
        headers=_headers(telegram_user_id),
        params={"query": query, "as_of_date": as_of_date, "limit": 20},
    )
    assert response.status_code == 200
    return response.json()["items"]


async def _clinic_trace(
    clinic_id: UUID,
    *,
    query: str,
    as_of_date: date,
) -> tuple[str, tuple[UUID, ...]]:
    engine = create_async_engine(database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_clinic_id', :clinic_id, true)"),
                {"clinic_id": str(clinic_id)},
            )
            repository = ApprovedClinicDocumentContextRepository(
                session,
                clinic_id=clinic_id,
            )
            fragments = await repository.search(
                query,
                as_of_date=as_of_date,
                limit=20,
            )
            return (
                clinic_document_context_trace_sha256(
                    fragments,
                    as_of_date=as_of_date,
                ),
                tuple(fragment.version_id for fragment in fragments),
            )
    finally:
        await engine.dispose()


def test_synthetic_fixture_pack_supports_time_travel_and_tenant_retrieval() -> None:
    admin_a = 9_300_000_001 + uuid4().int % 100_000_000
    admin_b = 9_400_000_001 + uuid4().int % 100_000_000
    clinic_a = _seed_admin(admin_a)
    _seed_admin(admin_b)

    with _client() as api:
        version_ids = _install_fixture_pack(api, admin_a)

        june_contracts = _search(api, admin_a, "договор", "2026-06-30")
        july_contracts = _search(api, admin_a, "договор", "2026-07-01")
        september_claims = _search(api, admin_a, "претенз", "2026-09-01")
        september_implant = _search(api, admin_a, "имплант", "2026-09-01")

        assert any(
            item["documentKey"] == "service-contract" and item["versionNo"] == 1
            for item in june_contracts
        )
        assert not any(
            item["documentKey"] == "service-contract" and item["versionNo"] == 2
            for item in june_contracts
        )
        assert any(
            item["documentKey"] == "service-contract" and item["versionNo"] == 2
            for item in july_contracts
        )
        assert not any(
            item["documentKey"] == "service-contract" and item["versionNo"] == 1
            for item in july_contracts
        )
        assert any(item["documentKey"] == "claims-policy" for item in september_claims)
        assert any(item["documentKey"] == "consent-implant" for item in september_implant)

        cross_tenant = _search(api, admin_b, "договор", "2026-09-01")
        assert cross_tenant == []

        warranty_version_id = UUID(version_ids[("warranty-main", 1)])
        trace_before, versions_before = asyncio.run(
            _clinic_trace(
                clinic_a,
                query="гарант",
                as_of_date=date(2026, 9, 1),
            )
        )
        assert warranty_version_id in versions_before

        blocked = api.post(
            f"/v1/clinic-documents/versions/{warranty_version_id}/approval-events",
            headers=_headers(admin_a),
            json={
                "decision": "BLOCKED",
                "reasonCode": "SYNTHETIC_FIXTURE_REVOKED",
            },
        )
        assert blocked.status_code == 201

        trace_after, versions_after = asyncio.run(
            _clinic_trace(
                clinic_a,
                query="гарант",
                as_of_date=date(2026, 9, 1),
            )
        )
        assert trace_after != trace_before
        assert warranty_version_id not in versions_after
