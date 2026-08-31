import hashlib
import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from legal_core.database import database_url, owner_database_url
from legal_core.main import create_app
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run clinic document API integration tests",
)


def seed_admin(telegram_user_id: int) -> tuple[UUID, UUID, UUID]:
    clinic_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO clinics (id,name) VALUES (:id,:name)"),
                {"id": clinic_id, "name": "Clinic documents API test"},
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
                    "(clinic_id,user_id,status,plan_code,starts_at,ends_at) VALUES "
                    "(:clinic_id,:user_id,'ACTIVE','MVP',"
                    "timezone('utc', now()) - INTERVAL '1 day',NULL)"
                ),
                {"clinic_id": clinic_id, "user_id": user_id},
            )
    finally:
        engine.dispose()
    return clinic_id, user_id, membership_id


def client() -> TestClient:
    engine = create_async_engine(database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return TestClient(
        create_app(
            session_factory=factory,
            managed_engine=engine,
            enable_draft_retention=False,
        )
    )


def headers(telegram_user_id: int) -> dict[str, str]:
    return {"X-Telegram-User-Id": str(telegram_user_id)}


def test_clinic_document_text_version_requires_approval_and_stays_in_tenant() -> None:
    admin_a = 8_500_000_001 + uuid4().int % 100_000_000
    admin_b = 8_600_000_001 + uuid4().int % 100_000_000
    clinic_a, _, _ = seed_admin(admin_a)
    seed_admin(admin_b)
    document_text = (
        "Гарантийное положение клиники.\n\n"
        "Гарантийный срок определяется условиями договора и медицинской документацией."
    )
    expected_sha = hashlib.sha256(document_text.encode()).hexdigest()

    with client() as api:
        created = api.post(
            "/v1/clinic-documents",
            headers=headers(admin_a),
            json={
                "documentKey": "warranty-main",
                "documentType": "WARRANTY_POLICY",
                "title": "Гарантийное положение",
            },
        )
        assert created.status_code == 201
        document_id = created.json()["id"]

        replayed = api.post(
            "/v1/clinic-documents",
            headers=headers(admin_a),
            json={
                "documentKey": "warranty-main",
                "documentType": "WARRANTY_POLICY",
                "title": "Гарантийное положение",
            },
        )
        assert replayed.status_code == 200
        assert replayed.json() == created.json()

        conflict = api.post(
            "/v1/clinic-documents",
            headers=headers(admin_a),
            json={
                "documentKey": "warranty-main",
                "documentType": "INFORMED_CONSENT",
                "title": "Другой документ",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "CLINIC_DOCUMENT_KEY_CONFLICT"

        version = api.post(
            f"/v1/clinic-documents/{document_id}/text-versions",
            headers=headers(admin_a),
            json={
                "sourceFilename": "warranty.txt",
                "normalizedText": document_text,
                "validFrom": "2026-01-01",
            },
        )
        assert version.status_code == 201
        version_id = version.json()["id"]
        assert version.json()["rawSha256"] == expected_sha
        assert version.json()["normalizedTextSha256"] == expected_sha
        assert version.json()["fragmentCount"] >= 1

        hidden_before_approval = api.get(
            "/v1/clinic-documents/fragments",
            headers=headers(admin_a),
            params={"query": "Гарантийный", "as_of_date": "2026-08-31"},
        )
        assert hidden_before_approval.status_code == 200
        assert hidden_before_approval.json() == {"items": []}

        cross_tenant_approval = api.post(
            f"/v1/clinic-documents/versions/{version_id}/approval-events",
            headers=headers(admin_b),
            json={"decision": "APPROVED", "reasonCode": "CLINIC_REVIEW_PASSED"},
        )
        assert cross_tenant_approval.status_code == 404

        approved = api.post(
            f"/v1/clinic-documents/versions/{version_id}/approval-events",
            headers=headers(admin_a),
            json={"decision": "APPROVED", "reasonCode": "CLINIC_REVIEW_PASSED"},
        )
        assert approved.status_code == 201
        assert approved.json()["decision"] == "APPROVED"

        visible = api.get(
            "/v1/clinic-documents/fragments",
            headers=headers(admin_a),
            params={"query": "Гарантийный", "as_of_date": "2026-08-31"},
        )
        assert visible.status_code == 200
        assert len(visible.json()["items"]) == 1
        assert visible.json()["items"][0]["documentId"] == document_id

        other_tenant = api.get(
            "/v1/clinic-documents/fragments",
            headers=headers(admin_b),
            params={"query": "Гарантийный"},
        )
        assert other_tenant.status_code == 200
        assert other_tenant.json() == {"items": []}

        blocked = api.post(
            f"/v1/clinic-documents/versions/{version_id}/approval-events",
            headers=headers(admin_a),
            json={"decision": "BLOCKED", "reasonCode": "CLINIC_DOCUMENT_REVOKED"},
        )
        assert blocked.status_code == 201

        hidden_after_block = api.get(
            "/v1/clinic-documents/fragments",
            headers=headers(admin_a),
            params={"query": "Гарантийный"},
        )
        assert hidden_after_block.status_code == 200
        assert hidden_after_block.json() == {"items": []}

    owner_engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with owner_engine.connect() as connection:
            version_hashes = connection.execute(
                text(
                    "SELECT raw_sha256, normalized_text_sha256 FROM clinic_document_versions "
                    "WHERE clinic_id=:clinic_id AND id=:version_id"
                ),
                {"clinic_id": clinic_a, "version_id": version_id},
            ).one()
            approval_hashes = connection.execute(
                text(
                    "SELECT expected_raw_sha256, expected_normalized_text_sha256 "
                    "FROM clinic_document_approval_events "
                    "WHERE clinic_id=:clinic_id AND version_id=:version_id "
                    "AND decision='APPROVED' ORDER BY created_at DESC, id DESC LIMIT 1"
                ),
                {"clinic_id": clinic_a, "version_id": version_id},
            ).one()
    finally:
        owner_engine.dispose()

    assert version_hashes == (expected_sha, expected_sha)
    assert approval_hashes == version_hashes
