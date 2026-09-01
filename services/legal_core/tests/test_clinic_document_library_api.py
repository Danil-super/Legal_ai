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
    reason="set POSTGRES_INTEGRATION=1 to run clinic document library integration tests",
)


def _seed_admin(telegram_user_id: int) -> tuple[UUID, UUID]:
    clinic_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO clinics (id,name) VALUES (:id,'Library test clinic')"),
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
                    "(:clinic_id,:user_id,'ACTIVE','MVP',timezone('utc', now())-INTERVAL '1 day')"
                ),
                {"clinic_id": clinic_id, "user_id": user_id},
            )
    finally:
        engine.dispose()
    return clinic_id, membership_id


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


def test_library_returns_only_current_tenant_and_latest_review_state() -> None:
    admin_a = 9_100_000_001 + uuid4().int % 100_000_000
    admin_b = 9_200_000_001 + uuid4().int % 100_000_000
    clinic_a, membership_a = _seed_admin(admin_a)
    clinic_b, membership_b = _seed_admin(admin_b)
    document_a = uuid4()
    version_a = uuid4()
    document_b = uuid4()
    version_b = uuid4()
    owner = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with owner.begin() as connection:
            for clinic_id, membership_id, document_id, version_id, key in (
                (clinic_a, membership_a, document_a, version_a, "contract-a"),
                (clinic_b, membership_b, document_b, version_b, "contract-b"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO clinic_documents "
                        "(id,clinic_id,document_key,document_type,title,created_by_membership_id) "
                        "VALUES (:id,:clinic_id,:key,'CONTRACT','Contract',:membership_id)"
                    ),
                    {
                        "id": document_id,
                        "clinic_id": clinic_id,
                        "key": key,
                        "membership_id": membership_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO clinic_document_versions "
                        "(id,clinic_id,document_id,version_no,source_filename,mime_type,"
                        "raw_object_key,raw_sha256,normalized_text,normalized_text_sha256,"
                        "created_by_membership_id) VALUES "
                        "(:id,:clinic_id,:document_id,1,'contract.txt','text/plain','test/key',"
                        ":raw_sha,'Contract text',:text_sha,:membership_id)"
                    ),
                    {
                        "id": version_id,
                        "clinic_id": clinic_id,
                        "document_id": document_id,
                        "raw_sha": "a" * 64 if clinic_id == clinic_a else "b" * 64,
                        "text_sha": "c" * 64 if clinic_id == clinic_a else "d" * 64,
                        "membership_id": membership_id,
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO clinic_document_approval_events "
                    "(clinic_id,version_id,actor_membership_id,decision,reason_code,"
                    "expected_raw_sha256,expected_normalized_text_sha256) VALUES "
                    "(:clinic_id,:version_id,:membership_id,'APPROVED','CLINIC_REVIEW_PASSED',"
                    ":raw_sha,:text_sha)"
                ),
                {
                    "clinic_id": clinic_a,
                    "version_id": version_a,
                    "membership_id": membership_a,
                    "raw_sha": "a" * 64,
                    "text_sha": "c" * 64,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO clinic_document_approval_events "
                    "(clinic_id,version_id,actor_membership_id,decision,reason_code,"
                    "expected_raw_sha256,expected_normalized_text_sha256) VALUES "
                    "(:clinic_id,:version_id,:membership_id,'BLOCKED','CLINIC_DOCUMENT_REVOKED',"
                    ":raw_sha,:text_sha)"
                ),
                {
                    "clinic_id": clinic_a,
                    "version_id": version_a,
                    "membership_id": membership_a,
                    "raw_sha": "a" * 64,
                    "text_sha": "c" * 64,
                },
            )
    finally:
        owner.dispose()

    with _client() as api:
        response = api.get(
            "/v1/clinic-document-library",
            headers={"X-Telegram-User-Id": str(admin_a)},
        )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == str(document_a)
    assert payload["items"][0]["documentKey"] == "contract-a"
    version = payload["items"][0]["versions"][0]
    assert version["id"] == str(version_a)
    assert version["reviewState"] == "BLOCKED"
    assert version["reviewReasonCode"] == "CLINIC_DOCUMENT_REVOKED"
    assert "normalizedText" not in version
    assert str(document_b) not in str(payload)
