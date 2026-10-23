"""Assignment must recheck case availability after real PostgreSQL lock waits."""

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


@pytest.mark.parametrize("blocked_resource", ["escalation", "recipient"])
@pytest.mark.parametrize("case_change", ["closed", "expired"])
def test_assignment_rechecks_case_after_lock_wait(blocked_resource, case_change) -> None:
    owner, escalation, clinic = seed_escalation()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with application_client() as client:
            target = _add_member(client, owner, clinic, owner + 2_000_000_000, "CLINIC_LAWYER")
            before = _history(escalation)
            with engine.connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
                transaction = blocker.begin()
                blocker.execute(text("SET LOCAL statement_timeout = '5s'"))
                blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
                lock_statement = (
                    "SELECT id FROM case_escalations WHERE id=:id FOR UPDATE"
                    if blocked_resource == "escalation"
                    else "SELECT id FROM clinic_users WHERE id=:id FOR UPDATE"
                )
                blocker.execute(text(lock_statement), {
                    "id": escalation if blocked_resource == "escalation" else target,
                })
                pending = pool.submit(
                    client.post,
                    f"/v1/case-escalations/{escalation}/assign",
                    headers=actor_headers(owner),
                    json={
                        "assignedMembershipId": str(target),
                        "expectedAssignedMembershipId": None,
                    },
                )
                try:
                    deadline = monotonic() + 10
                    blocked = False
                    while monotonic() < deadline:
                        blocked = bool(blocker.scalar(text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE :pid = ANY(pg_blocking_pids(pid)))"
                        ), {"pid": blocker_pid}))
                        if blocked:
                            break
                        assert not pending.done(), "assignment never reached the held row lock"
                        sleep(0.01)
                    assert blocked, "assignment did not block on the intended PostgreSQL row"
                    change = (
                        "status='CLOSED',closed_at=now()" if case_change == "closed"
                        else "retention_due_at=now()-interval '1 minute'"
                    )
                    blocker.execute(text(
                        f"UPDATE cases SET {change} "
                        "WHERE id=(SELECT case_id FROM case_escalations WHERE id=:id)"
                    ), {"id": escalation})
                    transaction.commit()
                finally:
                    if transaction.is_active:
                        transaction.rollback()
                response = pending.result(timeout=10)
            expected_status, expected_code = (
                (409, "CASE_CLOSED") if case_change == "closed"
                else (410, "CASE_CONTENT_EXPIRED")
            )
            assert response.status_code == expected_status, response.json()
            assert response.json()["error"]["code"] == expected_code
            assert _history(escalation) == before
    finally:
        engine.dispose()
