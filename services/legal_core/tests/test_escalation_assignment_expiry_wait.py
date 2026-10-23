"""Natural expiry must not be measured against PostgreSQL transaction start."""

import os
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep

import pytest
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from test_case_api import actor_headers, application_client
from test_escalation_assignment import _add_member, _history
from test_lawyer_workspace import seed_escalation

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="PostgreSQL integration"
)


@pytest.mark.parametrize("expiring", ["retention", "entitlement"])
def test_assignment_rechecks_natural_expiry_after_recipient_wait(expiring) -> None:
    owner, escalation, clinic = seed_escalation()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with application_client() as client:
            target = _add_member(client, owner, clinic, owner + 2_000_000_000, "CLINIC_LAWYER")
            with engine.begin() as connection:
                expiry = "clock_timestamp()+interval '2 seconds'"
                update = (
                    f"UPDATE cases SET retention_due_at={expiry} "
                    "WHERE id=(SELECT case_id FROM case_escalations WHERE id=:escalation) "
                    "RETURNING retention_due_at"
                    if expiring == "retention"
                    else f"UPDATE subscription_entitlements SET ends_at={expiry} "
                    "WHERE (clinic_id,user_id)=(SELECT clinic_id,user_id FROM clinic_users "
                    "WHERE id=:target) RETURNING ends_at"
                )
                expires_at = connection.scalar(
                    text(update), {"escalation": escalation, "target": target},
                )
            before = _history(escalation)
            with engine.connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
                blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
                blocker.execute(
                    text("SELECT id FROM clinic_users WHERE id=:id FOR UPDATE"), {"id": target},
                )
                pending = pool.submit(
                    client.post,
                    f"/v1/case-escalations/{escalation}/assign",
                    headers=actor_headers(owner),
                    json={
                        "assignedMembershipId": str(target), "expectedAssignedMembershipId": None,
                    },
                )
                try:
                    deadline = monotonic() + 5
                    blocked = False
                    while monotonic() < deadline:
                        blocked = bool(blocker.scalar(text(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                            "WHERE :pid=ANY(pg_blocking_pids(pid)))"
                        ), {"pid": blocker_pid}))
                        if blocked:
                            break
                        assert not pending.done(), "assignment did not reach the recipient lock"
                        sleep(0.01)
                    assert blocked, "assignment did not block on the held recipient"
                    assert blocker.scalar(
                        text("SELECT clock_timestamp() < :expiry"), {"expiry": expires_at},
                    )
                    while blocker.scalar(
                        text("SELECT clock_timestamp() <= :expiry"), {"expiry": expires_at},
                    ):
                        sleep(0.01)
                finally:
                    blocker.rollback()
                response = pending.result(timeout=10)
            expected_status, expected_code = (
                (410, "CASE_CONTENT_EXPIRED") if expiring == "retention"
                else (409, "LAWYER_UNAVAILABLE")
            )
            assert response.status_code == expected_status, response.json()
            assert response.json()["error"]["code"] == expected_code
            assert _history(escalation) == before
    finally:
        engine.dispose()
