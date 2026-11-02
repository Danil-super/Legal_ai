"""A routed case exposes recovery to its actual creator, regardless of clinic role."""

import os

import pytest

from test_case_api import actor_headers, application_client
from test_escalation_assignment import _add_member, _sql
from test_lawyer_workspace import seed_escalation
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="isolated PostgreSQL")


def test_escalation_detail_binds_recovery_to_resolved_membership(isolated_database, monkeypatch):
    monkeypatch.setenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "1")
    owner, escalation, clinic = seed_escalation()
    lawyer = owner + 2_000_000_000
    with application_client() as client:
        _add_member(client, owner, clinic, lawyer, "CLINIC_LAWYER")
        path = f"/v1/case-escalations/{escalation}"
        creator = client.get(path, headers=actor_headers(owner))
        reviewer = client.get(path, headers=actor_headers(lawyer))
        assert creator.status_code == reviewer.status_code == 200
        assert creator.json()["canRequestAiPlan"] is True
        assert reviewer.json()["canRequestAiPlan"] is False
        # A creator who is a lawyer has the same recovery capability.
        _sql("UPDATE clinic_users SET role='CLINIC_LAWYER' WHERE clinic_id=:clinic "
             "AND user_id=(SELECT id FROM users WHERE telegram_user_id=:actor)",
             clinic=clinic, actor=owner)
        assert client.get(path, headers=actor_headers(owner)).json()["canRequestAiPlan"] is True
