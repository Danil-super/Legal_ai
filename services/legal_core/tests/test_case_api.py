import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from legal_core.database import database_url, owner_database_url
from legal_core.case_retention import purge_expired_case_content
from legal_core.draft_retention import purge_expired_intake_drafts
from legal_core.main import create_app
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL API tests",
)


def seed_admin(
    telegram_user_id: int,
    *,
    entitlement_status: str | None = "ACTIVE",
    entitlement_is_expired: bool = False,
    role: str = "CLINIC_ADMIN",
) -> tuple[UUID, UUID]:
    clinic_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
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
                    "VALUES (:id,:clinic_id,:user_id,:role)"
                ),
                {
                    "id": membership_id,
                    "clinic_id": clinic_id,
                    "user_id": user_id,
                    "role": role,
                },
            )
            if entitlement_status is not None:
                ends_at_sql = (
                    "timezone('utc', now()) - INTERVAL '1 minute'"
                    if entitlement_is_expired
                    else "NULL"
                )
                connection.execute(
                    text(
                        "INSERT INTO subscription_entitlements "
                        "(clinic_id,user_id,status,plan_code,starts_at,ends_at) "
                        "VALUES (:clinic_id,:user_id,:status,'MVP',"
                        f"timezone('utc', now()) - INTERVAL '1 day',{ends_at_sql})"
                    ),
                    {
                        "clinic_id": clinic_id,
                        "user_id": user_id,
                        "status": entitlement_status,
                    },
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
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
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


def test_platform_owner_can_grant_access_by_telegram_id_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = 6_200_000_001 + uuid4().int % 100_000_000
    non_owner = 6_300_000_001 + uuid4().int % 100_000_000
    target = 6_400_000_001 + uuid4().int % 100_000_000
    seed_admin(owner)
    seed_admin(non_owner)
    monkeypatch.setenv("PLATFORM_OWNER_TELEGRAM_ID", str(owner))
    idempotency_key = uuid4()

    with application_client() as client:
        denied = client.post(
            "/v1/platform/subscription-grants",
            headers=actor_headers(non_owner, uuid4()),
            json={"telegramUserId": target},
        )
        granted = client.post(
            "/v1/platform/subscription-grants",
            headers=actor_headers(owner, idempotency_key),
            json={"telegramUserId": target},
        )
        replayed = client.post(
            "/v1/platform/subscription-grants",
            headers=actor_headers(owner, idempotency_key),
            json={"telegramUserId": target},
        )
        target_actor = client.get("/v1/actor", headers=actor_headers(target))

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "OWNER_REQUIRED"
    assert granted.status_code == 201
    assert granted.json() == {
        "telegramUserId": target,
        "clinicName": "Новая стоматология",
        "planCode": "MVP_MANUAL",
        "status": "ACTIVE",
        "endsAt": None,
    }
    assert replayed.status_code == 200
    assert replayed.json() == granted.json()
    assert target_actor.status_code == 200
    assert target_actor.json() == {"role": "CLINIC_ADMIN"}


def test_platform_owner_updates_the_target_single_existing_clinic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = 6_500_000_001 + uuid4().int % 100_000_000
    target = 6_600_000_001 + uuid4().int % 100_000_000
    seed_admin(owner)
    target_clinic_id, _ = seed_admin(target)
    monkeypatch.setenv("PLATFORM_OWNER_TELEGRAM_ID", str(owner))

    with application_client() as client:
        granted = client.post(
            "/v1/platform/subscription-grants",
            headers=actor_headers(owner, uuid4()),
            json={"telegramUserId": target},
        )

    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as connection:
            memberships = connection.scalar(
                text(
                    "SELECT count(*) FROM clinic_users cu "
                    "JOIN users u ON u.id = cu.user_id "
                    "WHERE u.telegram_user_id = :telegram_user_id "
                    "AND cu.status = 'ACTIVE' AND cu.role = 'CLINIC_ADMIN'"
                ),
                {"telegram_user_id": target},
            )
            entitlement = connection.execute(
                text(
                    "SELECT se.clinic_id, se.plan_code, se.status "
                    "FROM subscription_entitlements se "
                    "JOIN users u ON u.id = se.user_id "
                    "WHERE u.telegram_user_id = :telegram_user_id"
                ),
                {"telegram_user_id": target},
            ).one()
    finally:
        engine.dispose()

    assert granted.status_code == 201
    assert granted.json()["clinicName"] == "Синтетическая тестовая клиника"
    assert memberships == 1
    assert entitlement == (target_clinic_id, "MVP_MANUAL", "ACTIVE")


def test_platform_owner_can_grant_a_time_limited_free_pilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = 6_700_000_001 + uuid4().int % 100_000_000
    target = 6_800_000_001 + uuid4().int % 100_000_000
    seed_admin(owner)
    monkeypatch.setenv("PLATFORM_OWNER_TELEGRAM_ID", str(owner))

    with application_client() as client:
        granted = client.post(
            "/v1/platform/subscription-grants",
            headers=actor_headers(owner, uuid4()),
            json={
                "telegramUserId": target,
                "planCode": "FREE_PILOT",
                "pilotDays": 30,
            },
        )
        target_actor = client.get("/v1/actor", headers=actor_headers(target))

    assert granted.status_code == 201
    assert granted.json()["planCode"] == "FREE_PILOT"
    assert granted.json()["status"] == "ACTIVE"
    assert granted.json()["endsAt"] is not None
    assert datetime.fromisoformat(granted.json()["endsAt"].replace("Z", "+00:00")) > datetime.now(
        UTC
    )
    assert target_actor.status_code == 200


def test_telegram_intake_drafts_are_resumable_private_and_versioned() -> None:
    administrator = 6_900_000_001 + uuid4().int % 100_000_000
    other_administrator = 7_000_000_001 + uuid4().int % 100_000_000
    seed_admin(administrator)
    seed_admin(other_administrator)

    with application_client() as client:
        created = client.post(
            "/v1/telegram-intake-drafts",
            headers=actor_headers(administrator, uuid4()),
            json={},
        )
        draft_id = created.json()["id"]
        listed_before_update = client.get(
            "/v1/telegram-intake-drafts", headers=actor_headers(administrator)
        )
        saved = client.put(
            f"/v1/telegram-intake-drafts/{draft_id}",
            headers=actor_headers(administrator, uuid4()),
            json={
                "expectedRevision": 1,
                "wizardState": "SERVICE_TYPE",
                "draftData": {"incident_type": "QUALITY_COMPLAINT"},
            },
        )
        denied = client.get(
            f"/v1/telegram-intake-drafts/{draft_id}", headers=actor_headers(other_administrator)
        )
        stale = client.put(
            f"/v1/telegram-intake-drafts/{draft_id}",
            headers=actor_headers(administrator, uuid4()),
            json={
                "expectedRevision": 1,
                "wizardState": "SERVICE_TYPE",
                "draftData": {"incident_type": "QUALITY_COMPLAINT"},
            },
        )
        archived = client.post(
            f"/v1/telegram-intake-drafts/{draft_id}/archive",
            headers=actor_headers(administrator, uuid4()),
            json={"expectedRevision": 2},
        )
        listed_after_archive = client.get(
            "/v1/telegram-intake-drafts", headers=actor_headers(administrator)
        )

    assert created.status_code == 201
    assert created.json()["wizardState"] == "INCIDENT"
    assert created.json()["draftData"] == {}
    assert listed_before_update.json()["items"] == [
        {
            "id": draft_id,
            "wizardState": "INCIDENT",
            "revision": 1,
            "incidentType": None,
            "updatedAt": created.json()["updatedAt"],
        }
    ]
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    assert saved.json()["draftData"] == {"incident_type": "QUALITY_COMPLAINT"}
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "INTAKE_DRAFT_NOT_FOUND"
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "INTAKE_DRAFT_REVISION_CONFLICT"
    assert archived.status_code == 200
    assert archived.json()["revision"] == 3
    assert listed_after_archive.json() == {"items": []}
    assert count_workflow_resources(administrator) == (0, 0, 0, 0)


def test_expired_telegram_intake_drafts_are_purged_without_creating_a_case() -> None:
    administrator = 7_100_000_001 + uuid4().int % 100_000_000
    seed_admin(administrator)

    with application_client() as client:
        created = client.post(
            "/v1/telegram-intake-drafts",
            headers=actor_headers(administrator, uuid4()),
            json={},
        )
        draft_id = created.json()["id"]

    sync_engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with sync_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE telegram_intake_drafts SET purge_after = "
                    "timezone('utc', now()) - INTERVAL '1 second' WHERE id = :id"
                ),
                {"id": draft_id},
            )
    finally:
        sync_engine.dispose()

    async_engine = create_async_engine(database_url())
    try:
        deleted = asyncio.run(
            purge_expired_intake_drafts(async_sessionmaker(async_engine, expire_on_commit=False))
        )
    finally:
        asyncio.run(async_engine.dispose())

    with application_client() as client:
        listed = client.get("/v1/telegram-intake-drafts", headers=actor_headers(administrator))

    assert created.status_code == 201
    assert deleted == 1
    assert listed.json() == {"items": []}
    assert count_workflow_resources(administrator) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    ("entitlement_status", "entitlement_is_expired"),
    [
        (None, False),
        ("SUSPENDED", False),
        ("CANCELLED", False),
        ("ACTIVE", True),
    ],
)
def test_actor_probe_requires_an_active_current_subscription(
    entitlement_status: str | None, entitlement_is_expired: bool
) -> None:
    admin = 6_100_000_001 + uuid4().int % 100_000_000
    seed_admin(
        admin,
        entitlement_status=entitlement_status,
        entitlement_is_expired=entitlement_is_expired,
    )

    with application_client() as client:
        denied = client.get("/v1/actor", headers=actor_headers(admin))
        protected = client.get(
            "/v1/legal/fragments",
            headers=actor_headers(admin),
            params={"query": "медицинских услуг", "as_of_date": "2026-08-22"},
        )

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "SUBSCRIPTION_INACTIVE"
    assert protected.status_code == 403
    assert protected.json()["error"]["code"] == "SUBSCRIPTION_INACTIVE"


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


def test_legal_library_is_visible_only_to_clinic_lawyer_and_owner() -> None:
    lawyer = 4_100_000_001 + uuid4().int % 100_000_000
    owner = 4_200_000_001 + uuid4().int % 100_000_000
    administrator = 4_300_000_001 + uuid4().int % 100_000_000
    seed_admin(lawyer, role="CLINIC_LAWYER")
    seed_admin(owner, role="CLINIC_OWNER")
    seed_admin(administrator)

    with application_client() as client:
        lawyer_response = client.get(
            "/v1/legal/library",
            headers=actor_headers(lawyer),
            params={"as_of_date": "2026-09-04"},
        )
        owner_response = client.get(
            "/v1/legal/library",
            headers=actor_headers(owner),
            params={"as_of_date": "2026-09-04"},
        )
        administrator_response = client.get(
            "/v1/legal/library",
            headers=actor_headers(administrator),
            params={"as_of_date": "2026-09-04"},
        )

    assert lawyer_response.status_code == 200
    assert owner_response.status_code == 200
    assert lawyer_response.json()["asOfDate"] == "2026-09-04"
    assert owner_response.json()["asOfDate"] == "2026-09-04"
    assert all("fragmentText" not in item for item in lawyer_response.json()["items"])
    assert administrator_response.status_code == 403
    assert administrator_response.json()["error"]["code"] == "LEGAL_LIBRARY_NOT_ALLOWED"


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

        report_before_confirmation = client.post(
            f"/v1/cases/{case_id}/reports",
            headers=actor_headers(admin_a, uuid4()),
            json={"locale": "ru-RU"},
        )
        assert report_before_confirmation.status_code == 409
        assert report_before_confirmation.json()["error"]["code"] == "CASE_NOT_FINALIZED"

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


def test_active_case_limit_blocks_the_sixth_unfinished_case() -> None:
    administrator = 7_200_000_001 + uuid4().int % 100_000_000
    seed_admin(administrator)
    payload = {"intakeSchemaVersion": "dental-case-intake.v1", "channel": "TELEGRAM"}

    with application_client() as client:
        created = [
            client.post("/v1/cases", headers=actor_headers(administrator, uuid4()), json=payload)
            for _ in range(5)
        ]
        blocked = client.post(
            "/v1/cases", headers=actor_headers(administrator, uuid4()), json=payload
        )

    assert [response.status_code for response in created] == [201] * 5
    assert blocked.status_code == 409
    assert blocked.json()["error"] == {
        "code": "ACTIVE_CASE_LIMIT_REACHED",
        "message": "The administrator active case limit was reached",
        "details": {"limit": 5, "activeCaseCount": 5},
        "correlationId": blocked.json()["error"]["correlationId"],
    }


def test_monthly_confirmed_case_limit_also_blocks_clinic_owner() -> None:
    owner = 7_300_000_001 + uuid4().int % 100_000_000
    clinic_id, membership_id = seed_admin(owner, role="CLINIC_OWNER")
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO cases "
                    "(clinic_id, created_by_membership_id, status, closed_at, retention_due_at) "
                    "SELECT :clinic_id, :membership_id, 'ANALYSIS_BLOCKED', "
                    "timezone('utc', now()), timezone('utc', now()) + INTERVAL '90 days' "
                    "FROM generate_series(1, 30)"
                ),
                {"clinic_id": clinic_id, "membership_id": membership_id},
            )
    finally:
        engine.dispose()

    with application_client() as client:
        blocked = client.post(
            f"/v1/telegram-case-workflows/{uuid4()}/submissions",
            headers=actor_headers(owner),
            json=workflow_submission(),
        )

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "CLINIC_MONTHLY_CASE_LIMIT_REACHED"
    assert blocked.json()["error"]["details"] == {
        "limit": 30,
        "confirmedCaseCount": 30,
        "period": "CURRENT_UTC_CALENDAR_MONTH",
    }


def test_retention_purges_confirmed_case_content_but_keeps_bounded_metadata() -> None:
    administrator = 7_400_000_001 + uuid4().int % 100_000_000
    seed_admin(administrator)
    workflow_id = uuid4()

    with application_client() as client:
        submitted = client.post(
            f"/v1/telegram-case-workflows/{workflow_id}/submissions",
            headers=actor_headers(administrator),
            json=workflow_submission(),
        )
    case_id = submitted.json()["case"]["id"]

    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE cases SET retention_due_at = timezone('utc', now()) - INTERVAL '1 second' "
                    "WHERE id = :case_id"
                ),
                {"case_id": case_id},
            )
    finally:
        engine.dispose()

    async_engine = create_async_engine(database_url())
    try:
        purged = asyncio.run(
            purge_expired_case_content(async_sessionmaker(async_engine, expire_on_commit=False))
        )
    finally:
        asyncio.run(async_engine.dispose())

    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as connection:
            retained_case = connection.execute(
                text(
                    "SELECT status, content_purged_at IS NOT NULL, title, incident_date, "
                    "retention_due_at FROM cases WHERE id = :case_id"
                ),
                {"case_id": case_id},
            ).one()
            child_counts = tuple(
                int(
                    connection.execute(
                        text(f"SELECT count(*) FROM {table} WHERE case_id = :case_id"),
                        {"case_id": case_id},
                    ).scalar_one()
                )
                for table in ("case_facts", "case_reports", "telegram_case_workflows")
            )
            retention_event = connection.execute(
                text(
                    "SELECT facts_purged, reports_purged, discussion_messages_purged "
                    "FROM case_retention_events WHERE case_id = :case_id"
                ),
                {"case_id": case_id},
            ).one()
    finally:
        engine.dispose()

    with application_client() as client:
        unavailable = client.get(f"/v1/cases/{case_id}", headers=actor_headers(administrator))

    assert submitted.status_code == 201
    assert purged == 1
    assert retained_case == ("CONTENT_PURGED", True, None, None, None)
    assert child_counts == (0, 0, 0)
    assert retention_event == (len(workflow_submission()["facts"]), 1, 0)
    assert unavailable.status_code == 410
    assert unavailable.json()["error"]["code"] == "CASE_CONTENT_PURGED"


def test_clinic_lawyer_cannot_create_or_submit_case_intake() -> None:
    lawyer = 7_500_000_001 + uuid4().int % 100_000_000
    seed_admin(lawyer, role="CLINIC_LAWYER")

    with application_client() as client:
        create_blocked = client.post(
            "/v1/cases",
            headers=actor_headers(lawyer, uuid4()),
            json={"intakeSchemaVersion": "dental-case-intake.v1", "channel": "TELEGRAM"},
        )
        submit_blocked = client.post(
            f"/v1/telegram-case-workflows/{uuid4()}/submissions",
            headers=actor_headers(lawyer),
            json=workflow_submission(),
        )

    assert create_blocked.status_code == 403
    assert submit_blocked.status_code == 403
    assert create_blocked.json()["error"]["code"] == "CASE_INTAKE_NOT_ALLOWED"
    assert submit_blocked.json()["error"]["code"] == "CASE_INTAKE_NOT_ALLOWED"


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
    summary_fact = next(fact for fact in changed_facts if fact["factKey"] == "PROBLEM_SUMMARY")
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
