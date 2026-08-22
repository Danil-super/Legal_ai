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
                "factKey": "PRIMARY_INCIDENT_TYPE",
                "valueType": "ENUM",
                "value": {"value": "CROWN_PROBLEM"},
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
                "factKey": "LAWYER_CONTACT",
                "valueType": "BOOLEAN",
                "value": {"state": "NO"},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "REGULATOR_OR_COURT",
                "valueType": "BOOLEAN",
                "value": {"boolean": False},
                "sourceType": "USER_STATEMENT",
            },
            {
                "factKey": "REGULATOR_THREAT",
                "valueType": "BOOLEAN",
                "value": {"state": "NO"},
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


def workflow_submission() -> dict[str, object]:
    batch = complete_fact_batch()
    return {
        "intakeSchemaVersion": batch["intakeSchemaVersion"],
        "locale": "ru-RU",
        "facts": batch["facts"],
    }


def count_workflow_resources(telegram_user_id: int) -> tuple[int, int, int, int]:
    engine = create_engine(database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as connection:
            clinic_id = connection.execute(
                text(
                    "SELECT cu.clinic_id FROM clinic_users cu "
                    "JOIN users u ON u.id=cu.user_id WHERE u.telegram_user_id=:telegram_user_id"
                ),
                {"telegram_user_id": telegram_user_id},
            ).scalar_one()
            values = [
                int(
                    connection.execute(
                        text(f"SELECT count(*) FROM {table} WHERE clinic_id=:clinic_id"),
                        {"clinic_id": clinic_id},
                    ).scalar_one()
                )
                for table in ("telegram_case_workflows", "cases", "case_facts", "case_reports")
            ]
    finally:
        engine.dispose()
    return values[0], values[1], values[2], values[3]


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


def test_unknown_dates_and_signals_are_persisted_without_becoming_negative_evidence() -> None:
    admin = 6_100_000_001 + uuid4().int % 100_000_000
    seed_admin(admin)
    batch = complete_fact_batch()
    facts = batch["facts"]
    assert isinstance(facts, list)
    by_key = {fact["factKey"]: fact for fact in facts}
    by_key["SERVICE_DATE"]["value"] = {"date": None, "precision": "UNKNOWN"}
    by_key["FORMAL_CLAIM"]["value"] = {"state": "UNKNOWN"}
    by_key["HARM_CLAIMED"]["value"] = {"state": "UNKNOWN"}
    by_key["REGULATOR_OR_COURT"]["value"] = {"state": "UNKNOWN"}
    by_key["REGULATOR_THREAT"]["value"] = {"state": "UNKNOWN"}
    facts.append(
        {
            "factKey": "HOSPITALIZATION",
            "valueType": "BOOLEAN",
            "value": {"state": "UNKNOWN"},
            "sourceType": "USER_STATEMENT",
        }
    )

    with application_client() as client:
        created = client.post(
            "/v1/cases",
            headers=actor_headers(admin, uuid4()),
            json={"intakeSchemaVersion": "dental-case-intake.v1", "channel": "TELEGRAM"},
        )
        case_id = created.json()["id"]
        recorded = client.post(
            f"/v1/cases/{case_id}/facts",
            headers=actor_headers(admin, uuid4()),
            json=batch,
        )
        report = client.post(
            f"/v1/cases/{case_id}/reports",
            headers=actor_headers(admin, uuid4()),
            json={"locale": "ru-RU"},
        )

    assert created.status_code == 201
    assert recorded.status_code == 200
    assert recorded.json()["missingFacts"] == []
    assert report.status_code == 201
    assert report.json()["reportJson"]["facts"]["SERVICE_DATE"] == {
        "date": None,
        "precision": "UNKNOWN",
    }
    assert report.json()["reportJson"]["facts"]["HARM_CLAIMED"] == "UNKNOWN"


def test_atomic_telegram_workflow_survives_lost_response_without_duplicate_resources() -> None:
    admin = 7_100_000_001 + uuid4().int % 100_000_000
    other_admin = 8_100_000_001 + uuid4().int % 100_000_000
    seed_admin(admin)
    seed_admin(other_admin)
    workflow_id = uuid4()
    payload = workflow_submission()

    with application_client() as first_process:
        first = first_process.post(
            f"/v1/telegram-case-workflows/{workflow_id}/submissions",
            headers=actor_headers(admin),
            json=payload,
        )
        assert first.status_code == 201
        first_body = first.json()
        assert first_body["workflowId"] == str(workflow_id)
        assert first_body["state"] == "SUCCEEDED"
        assert first_body["report"]["reportJson"]["legalBasis"]["status"] == "NOT_AVAILABLE"

        # Simulates a response lost after commit: Telegram retries the same callback.
        replay = first_process.post(
            f"/v1/telegram-case-workflows/{workflow_id}/submissions",
            headers=actor_headers(admin),
            json=payload,
        )
        assert replay.status_code == 200
        assert replay.json() == first_body

    # A fresh app/client has no process memory and recovers the persisted result.
    with application_client() as restarted_process:
        recovered = restarted_process.get(
            f"/v1/telegram-case-workflows/{workflow_id}",
            headers=actor_headers(admin),
        )
        hidden = restarted_process.get(
            f"/v1/telegram-case-workflows/{workflow_id}",
            headers=actor_headers(other_admin),
        )
        collision = restarted_process.post(
            f"/v1/telegram-case-workflows/{workflow_id}/submissions",
            headers=actor_headers(other_admin),
            json=payload,
        )

    assert recovered.status_code == 200
    assert recovered.json() == first_body
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "WORKFLOW_NOT_FOUND"
    assert collision.status_code == 409
    assert collision.json()["error"]["code"] == "WORKFLOW_ID_UNAVAILABLE"
    facts = payload["facts"]
    assert isinstance(facts, list)
    assert count_workflow_resources(admin) == (1, 1, len(facts), 1)
    assert count_workflow_resources(other_admin) == (0, 0, 0, 0)


def test_workflow_uuid_cannot_be_reused_for_changed_facts() -> None:
    admin = 7_200_000_001 + uuid4().int % 100_000_000
    seed_admin(admin)
    workflow_id = uuid4()
    original = workflow_submission()
    changed = workflow_submission()
    changed_facts = changed["facts"]
    assert isinstance(changed_facts, list)
    summary_fact = next(
        fact for fact in changed_facts if fact["factKey"] == "PROBLEM_SUMMARY"
    )
    summary_fact["value"]["text"] = "Иная обезличенная ситуация"

    with application_client() as client:
        created = client.post(
            f"/v1/telegram-case-workflows/{workflow_id}/submissions",
            headers=actor_headers(admin),
            json=original,
        )
        conflict = client.post(
            f"/v1/telegram-case-workflows/{workflow_id}/submissions",
            headers=actor_headers(admin),
            json=changed,
        )

    assert created.status_code == 201
    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "WORKFLOW_PAYLOAD_MISMATCH"
    facts = original["facts"]
    assert isinstance(facts, list)
    assert count_workflow_resources(admin)[1:] == (1, len(facts), 1)


def test_telegram_workflow_rejects_semantically_invalid_facts_before_persistence() -> None:
    admin = 7_300_000_001 + uuid4().int % 100_000_000
    seed_admin(admin)
    workflow_id = uuid4()
    payload = workflow_submission()
    facts = payload["facts"]
    assert isinstance(facts, list)
    facts[0] = {
        "factKey": "INCIDENT_TYPES",
        "valueType": "TEXT",
        "value": {"text": "Не набор типов инцидентов"},
        "sourceType": "USER_STATEMENT",
    }

    with application_client() as client:
        response = client.post(
            f"/v1/telegram-case-workflows/{workflow_id}/submissions",
            headers=actor_headers(admin),
            json=payload,
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert count_workflow_resources(admin) == (0, 0, 0, 0)
