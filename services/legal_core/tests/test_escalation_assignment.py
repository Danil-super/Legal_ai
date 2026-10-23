"""Real PostgreSQL contract for OWNER-mediated escalation assignment."""

import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from test_case_api import actor_headers, application_client, seed_admin
from test_lawyer_workspace import seed_escalation

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="PostgreSQL integration"
)


def _sql(statement: str, **params):
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            result = connection.execute(text(statement), params)
            return result.mappings().all() if result.returns_rows else []
    finally:
        engine.dispose()


def _membership(clinic: UUID, telegram_user: int) -> UUID:
    return _sql(
        "SELECT m.id FROM clinic_users m JOIN users u ON u.id=m.user_id "
        "WHERE m.clinic_id=:clinic AND u.telegram_user_id=:telegram",
        clinic=clinic,
        telegram=telegram_user,
    )[0]["id"]


def _add_member(client: TestClient, owner: int, clinic: UUID, user: int, role: str) -> UUID:
    created = client.post(
        "/v1/clinic/members",
        headers=actor_headers(owner),
        json={"telegramUserId": user, "role": role},
    )
    assert created.status_code == 201, created.json()
    return _membership(clinic, user)


def _history(escalation: UUID):
    events = _sql(
        "SELECT id,actor_membership_id,action FROM case_escalation_workflow_events "
        "WHERE escalation_id=:id ORDER BY sequence",
        id=escalation,
    )
    audits = _sql(
        "SELECT id,actor_membership_id,action,metadata_json FROM audit_events "
        "WHERE resource_id=:id ORDER BY created_at,id",
        id=escalation,
    )
    return events, audits


@pytest.fixture
def assignment():
    owner, escalation, clinic = seed_escalation()
    lawyer = owner + 2_000_000_000
    with application_client() as client:
        target = _add_member(client, owner, clinic, lawyer, "CLINIC_LAWYER")
        yield client, owner, escalation, clinic, lawyer, target


def test_owner_transfers_claim_and_keeps_true_actor_in_history_and_audit(assignment) -> None:
    client, owner, escalation, clinic, lawyer, target = assignment
    path = f"/v1/case-escalations/{escalation}"
    owner_membership = _membership(clinic, owner)
    assert client.post(path + "/claim", headers=actor_headers(owner)).status_code == 200
    before_events, before_audits = _history(escalation)
    payload = {
        "assignedMembershipId": str(target),
        "expectedAssignedMembershipId": str(owner_membership),
    }
    assigned = client.post(path + "/assign", headers=actor_headers(owner), json=payload)
    assert assigned.status_code == 200, assigned.json()
    assert assigned.json()["status"] == "IN_PROGRESS"
    assert assigned.json()["assignedMembershipId"] == str(target)
    assert assigned.json()["assignedToMe"] is False

    events, audits = _history(escalation)
    assert len(events) == len(before_events) + 1
    assert events[-1]["actor_membership_id"] == owner_membership
    assert len(audits) == len(before_audits) + 1
    assert audits[-1]["actor_membership_id"] == owner_membership
    assert str(target) in str(audits[-1]["metadata_json"])
    replay = client.post(path + "/assign", headers=actor_headers(owner), json=payload)
    assert replay.status_code == 200, replay.json()
    assert replay.json() == assigned.json()
    assert _history(escalation) == (events, audits)

    for user, assigned_to_me in ((owner, False), (lawyer, True)):
        detail = client.get(path, headers=actor_headers(user))
        assert detail.status_code == 200
        assert detail.json()["assignedMembershipId"] == str(target)
        assert detail.json()["assignedToMe"] is assigned_to_me
        queue = client.get("/v1/case-escalations", headers=actor_headers(user)).json()["items"]
        card = next(item for item in queue if item["escalationId"] == str(escalation))
        assert card["status"] == "IN_PROGRESS"
        assert card["assignedMembershipId"] == str(target)
        assert card["assignedToMe"] is assigned_to_me

    other_lawyer = owner + 4_000_000_000
    _add_member(client, owner, clinic, other_lawyer, "CLINIC_LAWYER")
    for caller in (owner, other_lawyer):
        assert client.post(path + "/claim", headers=actor_headers(caller)).status_code == 409
        assert _history(escalation) == (events, audits)
    denied = client.post(
        path + "/resolve", headers=actor_headers(owner), json={"body": "Синтетическое заключение"}
    )
    assert denied.status_code == 403
    assert _history(escalation) == (events, audits)
    resolved = client.post(
        path + "/resolve", headers=actor_headers(lawyer), json={"body": "Синтетическое заключение"}
    )
    assert resolved.status_code == 200, resolved.json()
    assert resolved.json()["status"] == "RESOLVED"


def test_owner_can_assign_required_escalation_with_explicit_null_expectation(assignment) -> None:
    client, owner, escalation, _, lawyer, target = assignment
    path = f"/v1/case-escalations/{escalation}"
    assigned = client.post(
        path + "/assign",
        headers=actor_headers(owner),
        json={"assignedMembershipId": str(target), "expectedAssignedMembershipId": None},
    )
    assert assigned.status_code == 200, assigned.json()
    detail = client.get(path, headers=actor_headers(lawyer)).json()
    assert detail["status"] == "IN_PROGRESS" and detail["assignedToMe"] is True


@pytest.mark.parametrize("role", ["CLINIC_LAWYER", "CLINIC_ADMIN"])
def test_only_owner_may_assign_even_when_lawyer_is_current_assignee(assignment, role) -> None:
    client, owner, escalation, clinic, lawyer, target = assignment
    caller = lawyer
    if role == "CLINIC_ADMIN":
        caller = owner + 4_000_000_000
        _add_member(client, owner, clinic, caller, role)
    path = f"/v1/case-escalations/{escalation}"
    assert client.post(path + "/claim", headers=actor_headers(lawyer)).status_code == 200
    before = _history(escalation)
    denied = client.post(
        path + "/assign",
        headers=actor_headers(caller),
        json={"assignedMembershipId": str(target), "expectedAssignedMembershipId": str(target)},
    )
    assert denied.status_code == 403, denied.json()
    assert _history(escalation) == before


def test_foreign_escalation_and_target_are_hidden_without_writes(assignment) -> None:
    client, owner, escalation, _, _, target = assignment
    outsider = owner + 6_000_000_000
    other_clinic, _ = seed_admin(outsider, role="CLINIC_OWNER")
    foreign_target = _add_member(
        client, outsider, other_clinic, outsider + 2_000_000_000, "CLINIC_LAWYER"
    )
    before = _history(escalation)
    path = f"/v1/case-escalations/{escalation}/assign"
    for caller, member in ((outsider, target), (owner, foreign_target), (owner, uuid4())):
        denied = client.post(
            path,
            headers=actor_headers(caller),
            json={"assignedMembershipId": str(member), "expectedAssignedMembershipId": None},
        )
        assert denied.status_code == 404, denied.json()
        assert _history(escalation) == before


def test_closed_case_cannot_receive_an_assignment(assignment) -> None:
    client, owner, escalation, _, _, target = assignment
    _sql(
        "UPDATE cases SET status='CLOSED' "
        "WHERE id=(SELECT case_id FROM case_escalations WHERE id=:id)",
        id=escalation,
    )
    before = _history(escalation)
    denied = client.post(
        f"/v1/case-escalations/{escalation}/assign",
        headers=actor_headers(owner),
        json={"assignedMembershipId": str(target), "expectedAssignedMembershipId": None},
    )
    assert denied.status_code == 409, denied.json()
    assert denied.json()["error"]["code"] == "CASE_CLOSED"
    assert _history(escalation) == before


def test_finalized_intake_is_not_a_closed_lawyer_case(assignment) -> None:
    client, owner, escalation, _, _, target = assignment
    # Production confirmation sets closed_at to start retention, before lawyer work.
    _sql(
        "UPDATE cases SET closed_at=now(),retention_due_at=now()+interval '90 days' "
        "WHERE id=(SELECT case_id FROM case_escalations WHERE id=:id)",
        id=escalation,
    )
    assigned = client.post(
        f"/v1/case-escalations/{escalation}/assign",
        headers=actor_headers(owner),
        json={"assignedMembershipId": str(target), "expectedAssignedMembershipId": None},
    )
    assert assigned.status_code == 200, assigned.json()
    assert assigned.json()["assignedMembershipId"] == str(target)


@pytest.mark.parametrize("unavailable", ["membership", "user", "role", "entitlement"])
def test_unavailable_target_is_rejected_without_history_or_audit(assignment, unavailable) -> None:
    client, owner, escalation, _, _, target = assignment
    statements = {
        "membership": "UPDATE clinic_users SET status='REVOKED' WHERE id=:id",
        "user": "UPDATE users SET status='INACTIVE' "
        "WHERE id=(SELECT user_id FROM clinic_users WHERE id=:id)",
        "role": "UPDATE clinic_users SET role='CLINIC_ADMIN' WHERE id=:id",
        "entitlement": "UPDATE subscription_entitlements SET status='SUSPENDED' "
        "WHERE (clinic_id,user_id)=(SELECT clinic_id,user_id FROM clinic_users WHERE id=:id)",
    }
    _sql(statements[unavailable], id=target)
    before = _history(escalation)
    denied = client.post(
        f"/v1/case-escalations/{escalation}/assign",
        headers=actor_headers(owner),
        json={"assignedMembershipId": str(target), "expectedAssignedMembershipId": None},
    )
    assert denied.status_code == 409, denied.json()
    assert denied.json()["error"]["code"] == "LAWYER_UNAVAILABLE"
    assert _history(escalation) == before


def test_stale_expected_assignment_cannot_overwrite_an_existing_claim(assignment) -> None:
    client, owner, escalation, clinic, _, target = assignment
    path = f"/v1/case-escalations/{escalation}"
    assert client.post(path + "/claim", headers=actor_headers(owner)).status_code == 200
    before = _history(escalation)
    denied = client.post(
        path + "/assign",
        headers=actor_headers(owner),
        json={"assignedMembershipId": str(target), "expectedAssignedMembershipId": None},
    )
    assert denied.status_code == 409, denied.json()
    assert denied.json()["error"]["code"] == "ESCALATION_ASSIGNMENT_CHANGED"
    assert _history(escalation) == before
    assert client.get(path, headers=actor_headers(owner)).json()["assignedMembershipId"] == str(
        _membership(clinic, owner)
    )


def test_resolved_escalation_cannot_be_assigned(assignment) -> None:
    client, owner, escalation, clinic, _, target = assignment
    path = f"/v1/case-escalations/{escalation}"
    assert client.post(path + "/claim", headers=actor_headers(owner)).status_code == 200
    assert client.post(
        path + "/resolve", headers=actor_headers(owner), json={"body": "Синтетическое заключение"}
    ).status_code == 200
    before = _history(escalation)
    denied = client.post(
        path + "/assign",
        headers=actor_headers(owner),
        json={
            "assignedMembershipId": str(target),
            "expectedAssignedMembershipId": str(_membership(clinic, owner)),
        },
    )
    assert denied.status_code == 409, denied.json()
    assert _history(escalation) == before


def test_assignment_requires_explicit_expected_assignee(assignment) -> None:
    client, owner, escalation, _, _, target = assignment
    before = _history(escalation)
    denied = client.post(
        f"/v1/case-escalations/{escalation}/assign",
        headers=actor_headers(owner),
        json={"assignedMembershipId": str(target)},
    )
    assert denied.status_code == 422, denied.json()
    assert _history(escalation) == before


def test_expired_case_cannot_be_assigned_before_physical_purge(assignment) -> None:
    client, owner, escalation, _, _, target = assignment
    _sql(
        "UPDATE cases SET retention_due_at=now()-interval '1 minute' "
        "WHERE id=(SELECT case_id FROM case_escalations WHERE id=:id)",
        id=escalation,
    )
    before = _history(escalation)
    denied = client.post(
        f"/v1/case-escalations/{escalation}/assign",
        headers=actor_headers(owner),
        json={"assignedMembershipId": str(target), "expectedAssignedMembershipId": None},
    )
    assert denied.status_code == 410, denied.json()
    assert _history(escalation) == before
