import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from legal_core.database import database_url
from legal_core.main import create_app
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL API tests",
)


def seed_admin(telegram_user_id: int) -> tuple[UUID, UUID]:
    clinic_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    engine = create_engine(database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO clinics (id,name) VALUES (:id,:name)"),
                {"id": clinic_id, "name": "Синтетическая тестовая клиника"},
            )
            connection.execute(
                text("INSERT INTO users (id,telegram_user_id) VALUES (:id,:telegram_user_id)"),
                {"id": user_id, "telegram_user_id": telegram_user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO clinic_users (id,clinic_id,user_id,role) "
                    "VALUES (:id,:clinic_id,:user_id,'CLINIC_ADMIN')"
                ),
                {"id": membership_id, "clinic_id": clinic_id, "user_id": user_id},
            )
    finally:
        engine.dispose()
    return clinic_id, membership_id


def application_client() -> TestClient:
    engine = create_async_engine(database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return TestClient(create_app(session_factory=factory, managed_engine=engine))


def actor_headers(telegram_user_id: int, idempotency_key: UUID | None = None) -> dict[str, str]:
    headers = {"X-Telegram-User-Id": str(telegram_user_id)}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = str(idempotency_key)
    return headers


def complete_fact_batch() -> dict[str, object]:
    return {
        "questionId": "complete_synthetic_intake",
        "intakeSchemaVersion": "dental-case-intake.v1",
        "facts": [
            {
                "factKey": "INCIDENT_TYPES",
                "valueType": "ENUM_SET",
                "value": {"values": ["CROWN_PROBLEM"]},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "SERVICE_TYPE",
                "valueType": "TEXT",
                "value": {"text": "Установка коронки"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "SERVICE_DATE",
                "valueType": "DATE",
                "value": {"date": "2026-06-01", "precision": "EXACT"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "INCIDENT_DATE",
                "valueType": "DATE",
                "value": {"date": "2026-07-01", "precision": "EXACT"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "CLAIM_DATE",
                "valueType": "DATE",
                "value": {"date": "2026-07-02", "precision": "EXACT"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "PROBLEM_SUMMARY",
                "valueType": "TEXT",
                "value": {"text": "Пациент сообщил о сколе конструкции."},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "PATIENT_DEMAND",
                "valueType": "ENUM_SET",
                "value": {"values": ["NO_SPECIFIC_DEMAND"]},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "FORMAL_CLAIM",
                "valueType": "BOOLEAN",
                "value": {"boolean": False},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "HARM_CLAIMED",
                "valueType": "BOOLEAN",
                "value": {"boolean": False},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "REGULATOR_OR_COURT",
                "valueType": "BOOLEAN",
                "value": {"boolean": False},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "CLINIC_DOCUMENTS",
                "valueType": "DOCUMENT_INVENTORY",
                "value": {"CONTRACT": "AVAILABLE", "MEDICAL_RECORD": "AVAILABLE"},
                "sourceType": "USER_STATEMENT",
            },
        ],
    }


def test_actor_probe_authorizes_only_mapped_clinic_admin() -> None:
    admin = 6_000_000_001 + uuid4().int % 100_000_000
    unknown = 5_000_000_001 + uuid4().int % 100_000_000
    seed_admin(admin)

    with application_client() as client:
        allowed = client.get("/v1/actor", headers=actor_headers(admin))
        denied = client.get("/v1/actor", headers=actor_headers(unknown))

    assert allowed.status_code == 200
    assert allowed.json() == {"role": "CLINIC_ADMIN"}
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ACTOR_NOT_AUTHORIZED"


def test_legal_fragment_search_is_authenticated_validated_and_approved_only() -> None:
    admin = 4_000_000_001 + uuid4().int % 100_000_000
    unknown = 3_000_000_001 + uuid4().int % 100_000_000
    seed_admin(admin)

    with application_client() as client:
        pending_is_hidden = client.get(
            "/v1/legal/fragments",
            headers=actor_headers(admin),
            params={"query": "медицинских услуг", "as_of_date": "2026-08-22"},
        )
        invalid = client.get(
            "/v1/legal/fragments",
            headers=actor_headers(admin),
            params={"query": " ", "as_of_date": "2026-08-22"},
        )
        denied = client.get(
            "/v1/legal/fragments",
            headers=actor_headers(unknown),
            params={"query": "медицинских услуг", "as_of_date": "2026-08-22"},
        )

    assert pending_is_hidden.status_code == 200
    assert pending_is_hidden.json() == {"items": []}
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert denied.status_code == 403


def test_case_intake_report_and_cross_tenant_boundary() -> None:
    admin_a = 7_000_000_001 + uuid4().int % 100_000_000
    admin_b = 8_000_000_001 + uuid4().int % 100_000_000
    seed_admin(admin_a)
    seed_admin(admin_b)

    with application_client() as client:
        create_key = uuid4()
        create_response = client.post(
            "/v1/cases",
            headers=actor_headers(admin_a, create_key),
            json={"intakeSchemaVersion": "dental-case-intake.v1", "channel": "TELEGRAM"},
        )
        assert create_response.status_code == 201
        created = create_response.json()
        case_id = created["id"]
        assert created["status"] == "COLLECTING"
        assert created["publicNumber"].startswith("DL-2026-")

        replay = client.post(
            "/v1/cases",
            headers=actor_headers(admin_a, create_key),
            json={"intakeSchemaVersion": "dental-case-intake.v1", "channel": "TELEGRAM"},
        )
        assert replay.status_code == 200
        assert replay.json() == created

        denied = client.get(f"/v1/cases/{case_id}", headers=actor_headers(admin_b))
        assert denied.status_code == 404
        assert denied.json()["error"]["code"] == "CASE_NOT_FOUND"

        facts_response = client.post(
            f"/v1/cases/{case_id}/facts",
            headers=actor_headers(admin_a, uuid4()),
            json=complete_fact_batch(),
        )
        assert facts_response.status_code == 200
        assert facts_response.json()["missingFacts"] == []

        finalised = client.post(
            f"/v1/cases/{case_id}/intake-finalizations",
            headers=actor_headers(admin_a, uuid4()),
            json={},
        )
        assert finalised.status_code == 200
        assert finalised.json()["status"] == "ANALYSIS_BLOCKED"

        report_response = client.post(
            f"/v1/cases/{case_id}/reports",
            headers=actor_headers(admin_a, uuid4()),
            json={"locale": "ru-RU"},
        )
        assert report_response.status_code == 201
        report = report_response.json()
        assert report["reportJson"]["schemaVersion"] == "dental-case-report.v1"
        assert report["reportJson"]["legalBasis"]["status"] == "NOT_AVAILABLE"

        pdf = client.get(
            f"/v1/reports/{report['id']}/pdf",
            headers=actor_headers(admin_a),
        )
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF-")


def test_unknown_telegram_user_is_denied_without_tenant_details() -> None:
    with application_client() as client:
        response = client.post(
            "/v1/cases",
            headers=actor_headers(9_999_999_999, uuid4()),
            json={"intakeSchemaVersion": "dental-case-intake.v1", "channel": "TELEGRAM"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACTOR_NOT_AUTHORIZED"
