"""Intake finalization is not resolution of the lawyer's case workflow."""

import os
from uuid import uuid4

import pytest

from test_case_api import actor_headers, application_client, complete_fact_batch
from test_escalation_assignment import _add_member, _sql
from test_lawyer_workspace import seed_escalation

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="PostgreSQL integration"
)


def test_owner_can_assign_case_after_real_intake_finalization() -> None:
    owner, escalation, clinic = seed_escalation()
    case = _sql("SELECT case_id FROM case_escalations WHERE id=:id", id=escalation)[0]["case_id"]
    with application_client() as client:
        target = _add_member(client, owner, clinic, owner + 2_000_000_000, "CLINIC_LAWYER")
        recorded = client.post(
            f"/v1/cases/{case}/facts", headers=actor_headers(owner, uuid4()),
            json=complete_fact_batch(),
        )
        assert recorded.status_code == 200, recorded.json()
        finalized = client.post(
            f"/v1/cases/{case}/intake-finalizations",
            headers=actor_headers(owner, uuid4()), json={},
        )
        assert finalized.status_code == 200, finalized.json()
        stored = _sql("SELECT status,closed_at FROM cases WHERE id=:id", id=case)[0]
        assert stored["closed_at"] is not None
        assert stored["status"] != "CLOSED"
        response = client.post(
            f"/v1/case-escalations/{escalation}/assign", headers=actor_headers(owner),
            json={"assignedMembershipId": str(target), "expectedAssignedMembershipId": None},
        )
        assert response.status_code == 200, response.json()
        assert response.json()["assignedMembershipId"] == str(target)
