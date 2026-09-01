import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from legal_core.clinic_document_retrieval import plan_clinic_document_queries
from legal_core.contracts import FactKey
from legal_core.database import database_url, owner_database_url
from legal_core.main import create_app
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run synthetic clinic fixture retrieval tests",
)

ROOT = Path(__file__).parents[3]
FIXTURE_DIR = ROOT / "services/legal_core/fixtures/clinic_documents"
MANIFEST = FIXTURE_DIR / "synthetic_pack.v1.json"


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
                    "(:clinic_id,:user_id,'ACTIVE','MVP',timezone('utc', now())-INTERVAL '1 day')"
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


def _scenario_facts(scenario: dict[str, object]) -> dict[FactKey, object]:
    facts: dict[FactKey, object] = {
        FactKey.SERVICE_TYPE: scenario["service_type"],
        FactKey.INCIDENT_TYPES: scenario["incident_markers"],
        FactKey.PATIENT_DEMAND: scenario["patient_demand"],
    }
    if scenario.get("formal_claim") is True:
        facts[FactKey.FORMAL_CLAIM] = True
    return facts


def _load_pack() -> dict[str, object]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_synthetic_pack_roundtrips_through_approval_and_tenant_retrieval() -> None:
    admin = 9_300_000_001 + uuid4().int % 100_000_000
    _seed_admin(admin)
    pack = _load_pack()
    documents = pack["documents"]
    scenarios = pack["regression_scenarios"]
    assert isinstance(documents, list)
    assert isinstance(scenarios, list)

    with _client() as api:
        for raw_document in documents:
            assert isinstance(raw_document, dict)
            filename = raw_document["filename"]
            assert isinstance(filename, str)
            body = (FIXTURE_DIR / filename).read_text(encoding="utf-8")

            created = api.post(
                "/v1/clinic-documents",
                headers=_headers(admin),
                json={
                    "documentKey": raw_document["document_key"],
                    "documentType": raw_document["document_type"],
                    "title": raw_document["title"],
                },
            )
            assert created.status_code == 201
            document_id = UUID(created.json()["id"])

            version = api.post(
                f"/v1/clinic-documents/{document_id}/text-versions",
                headers=_headers(admin),
                json={
                    "sourceFilename": filename,
                    "normalizedText": body,
                    "validFrom": raw_document["valid_from"],
                },
            )
            assert version.status_code == 201
            version_id = UUID(version.json()["id"])

            approved = api.post(
                f"/v1/clinic-documents/versions/{version_id}/approval-events",
                headers=_headers(admin),
                json={
                    "decision": "APPROVED",
                    "reasonCode": "SYNTHETIC_FIXTURE_REVIEW_PASSED",
                },
            )
            assert approved.status_code == 201
            assert approved.json()["decision"] == "APPROVED"

        for scenario in scenarios:
            assert isinstance(scenario, dict)
            planned_queries = plan_clinic_document_queries(_scenario_facts(scenario))
            discovered: set[str] = set()
            for query in planned_queries:
                response = api.get(
                    "/v1/clinic-documents/fragments",
                    headers=_headers(admin),
                    params={
                        "query": query,
                        "as_of_date": "2026-08-31",
                        "limit": 20,
                    },
                )
                assert response.status_code == 200
                for item in response.json()["items"]:
                    discovered.add(item["documentKey"])

            expected = scenario["expected_document_keys"]
            assert isinstance(expected, list)
            assert set(expected) <= discovered, (
                scenario["scenario_id"],
                planned_queries,
                discovered,
            )
