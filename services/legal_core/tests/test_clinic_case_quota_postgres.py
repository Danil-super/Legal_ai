"""Real tenant-scoped quota decisions with the existing serialized confirmation path."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from test_case_api import actor_headers, application_client, seed_admin, workflow_submission
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL",
)


def seed_confirmed(clinic, membership, count):
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO cases (clinic_id,created_by_membership_id,status,closed_at,"
                "retention_due_at) SELECT :clinic,:membership,'ANALYSIS_BLOCKED',"
                "timezone('utc',now()),timezone('utc',now())+INTERVAL '90 days' "
                "FROM generate_series(1,:count)"
            ), {"clinic": clinic, "membership": membership, "count": count})
    finally:
        engine.dispose()


def configure(monkeypatch, clinic):
    monkeypatch.setenv("CASE_MONTHLY_LIMIT_OVERRIDE_CLINIC_ID", str(clinic))
    monkeypatch.setenv("CASE_MONTHLY_LIMIT_OVERRIDE_VALUE", "100")


@pytest.mark.parametrize("role", ["CLINIC_OWNER", "CLINIC_ADMIN"])
def test_configured_clinic_can_confirm_case_31_but_not_101(isolated_database, monkeypatch, role):
    actor = 96_000_000_000 + uuid4().int % 100_000_000
    clinic, membership = seed_admin(actor, role=role)
    configure(monkeypatch, clinic)
    seed_confirmed(clinic, membership, 30)
    workflow = uuid4()
    with application_client() as client:
        created = client.post(f"/v1/telegram-case-workflows/{workflow}/submissions",
                              headers=actor_headers(actor), json=workflow_submission())
        assert created.status_code == 201, created.text
        seed_confirmed(clinic, membership, 69)
        blocked = client.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                              headers=actor_headers(actor), json=workflow_submission())
        assert blocked.status_code == 429, blocked.text
        assert blocked.json()["error"]["code"] == "CLINIC_MONTHLY_CASE_LIMIT_REACHED"
        assert blocked.json()["error"]["details"] == {
            "limit": 100, "confirmedCaseCount": 100, "period": "CURRENT_UTC_CALENDAR_MONTH",
        }
        replay = client.post(f"/v1/telegram-case-workflows/{workflow}/submissions",
                             headers=actor_headers(actor), json=workflow_submission())
        assert replay.status_code == 200 and replay.json() == created.json()


def test_other_clinic_keeps_30_even_with_a_forged_clinic_header(isolated_database, monkeypatch):
    actor = 96_000_000_000 + uuid4().int % 100_000_000
    clinic, membership = seed_admin(actor, role="CLINIC_OWNER")
    target = uuid4()
    configure(monkeypatch, target)
    seed_confirmed(clinic, membership, 30)
    with application_client() as client:
        blocked = client.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
            headers={**actor_headers(actor), "X-Clinic-Id": str(target)},
            json=workflow_submission())
    assert blocked.status_code == 429, blocked.text
    assert blocked.json()["error"]["details"]["limit"] == 30


def test_malformed_operator_override_fails_closed_without_echoing_config(
    isolated_database, monkeypatch,
):
    actor = 96_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    marker = "synthetic-invalid-quota-marker"
    monkeypatch.setenv("CASE_MONTHLY_LIMIT_OVERRIDE_CLINIC_ID", marker)
    monkeypatch.setenv("CASE_MONTHLY_LIMIT_OVERRIDE_VALUE", "100")
    with application_client() as client:
        result = client.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                             headers=actor_headers(actor), json=workflow_submission())
    assert result.status_code == 503, result.text
    assert result.json()["error"]["code"] == "CASE_QUOTA_CONFIGURATION_UNAVAILABLE"
    assert marker not in result.text


def test_override_does_not_grant_an_inactive_subscription(isolated_database, monkeypatch):
    actor = 96_000_000_000 + uuid4().int % 100_000_000
    clinic, _membership = seed_admin(actor, entitlement_status=None)
    configure(monkeypatch, clinic)
    with application_client() as client:
        result = client.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                             headers=actor_headers(actor), json=workflow_submission())
    assert result.status_code == 403, result.text
    assert result.json()["error"]["code"] == "SUBSCRIPTION_INACTIVE"
