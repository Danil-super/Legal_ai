"""Independent public discovery/consent adversarial checks; synthetic data only."""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from legal_core.main import create_app
from test_case_api import actor_headers, application_client, seed_admin
from test_intake_preparation_acceptance import _proposal
from test_intake_preparation_lifecycle import _run, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires reviewer disposable PostgreSQL")
__all__ = ["draft_owner"]


def test_discovery_rechecks_current_access_after_operation_lock_wait(draft_owner):
    actor, clinic, _, draft = draft_owner
    result, _ = _proposal(actor, draft)
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as holder:
            holder.execute(text("SELECT id FROM idempotency_records WHERE id=:id FOR UPDATE"),
                           {"id": result.operation_id})
            blocker = holder.scalar(text("SELECT pg_backend_pid()"))

            async def scenario(sessions):
                app = create_app(session_factory=sessions, managed_engine=sessions.kw["bind"],
                                 enable_analysis_worker=False, enable_draft_retention=False)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://core.test") as client:
                    task = asyncio.create_task(client.get(
                        f"/v1/telegram-intake-drafts/{draft['id']}/understanding",
                        headers=actor_headers(actor),
                    ))
                    try:
                        blocked = False
                        for _ in range(200):
                            holder.execute(text("SELECT pg_stat_clear_snapshot()"))
                            blocked = holder.scalar(text(
                                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                "WHERE :pid = ANY(pg_blocking_pids(pid)))"
                            ), {"pid": blocker})
                            if blocked:
                                break
                            await asyncio.sleep(.01)
                        assert blocked, "discovery must actually wait on the operation lock"
                        holder.execute(text(
                            "UPDATE subscription_entitlements SET status='SUSPENDED' "
                            "WHERE clinic_id=:clinic"
                        ), {"clinic": clinic})
                        holder.commit()
                        response = await asyncio.wait_for(task, timeout=3)
                        assert response.status_code == 403, response.text
                        assert "suggestions" not in response.json()
                    finally:
                        holder.rollback()
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            _run(scenario)
    finally:
        engine.dispose()


@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize("same_clinic", [False, True])
def test_public_discovery_and_acceptance_require_current_exact_creator(
    draft_owner, accepted, same_clinic,
):
    actor, clinic, _, draft = draft_owner
    result, request = _proposal(actor, draft)
    caller = 24_800_000_000 + uuid4().int % 100_000_000
    other_clinic, other_member = seed_admin(caller)
    engine = create_engine(owner_database_url())
    try:
        if same_clinic:
            with engine.begin() as connection:
                connection.execute(text(
                    "DELETE FROM subscription_entitlements WHERE clinic_id=:id"
                ), {"id": other_clinic})
                connection.execute(text("UPDATE clinic_users SET clinic_id=:clinic WHERE id=:id"),
                                   {"clinic": clinic, "id": other_member})
                connection.execute(text(
                    "INSERT INTO subscription_entitlements "
                    "(clinic_id,user_id,status,plan_code,starts_at) "
                    "SELECT clinic_id,user_id,'ACTIVE','MVP',now()-interval '1 day' "
                    "FROM clinic_users WHERE id=:id"
                ), {"id": other_member})
        with application_client() as client:
            path = f"/v1/intake-preparations/{result.operation_id}/acceptance"
            if accepted:
                response = client.post(path, headers=actor_headers(actor),
                                       json=request.model_dump(mode="json", by_alias=True))
                assert response.status_code == 200
            denied = client.post(path, headers=actor_headers(caller),
                                 json=request.model_dump(mode="json", by_alias=True))
            assert denied.status_code == 404, denied.text
            denied = client.get(f"/v1/telegram-intake-drafts/{draft['id']}/understanding",
                                headers=actor_headers(caller))
            assert denied.status_code == 404, denied.text
            assert "sourceSpans" not in denied.text
    finally:
        engine.dispose()


@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize("change", ["member", "subscription", "expired", "archived", "submitted"])
def test_public_consent_and_discovery_recheck_access_and_closed_owner(
    draft_owner, accepted, change,
):
    actor, clinic, member, draft = draft_owner
    result, request = _proposal(actor, draft)
    engine = create_engine(owner_database_url())
    try:
        with application_client() as client:
            path = f"/v1/intake-preparations/{result.operation_id}/acceptance"
            payload = request.model_dump(mode="json", by_alias=True)
            if accepted:
                response = client.post(path, headers=actor_headers(actor), json=payload)
                assert response.status_code == 200
            with engine.begin() as connection:
                statements = {
                    "member": "UPDATE clinic_users SET status='REVOKED' WHERE id=:member",
                    "subscription": "UPDATE subscription_entitlements SET status='SUSPENDED' "
                                    "WHERE clinic_id=:clinic",
                    "expired": "UPDATE telegram_intake_drafts SET "
                               "purge_after=clock_timestamp()-interval '1 second' WHERE id=:id",
                    "archived": "UPDATE telegram_intake_drafts SET status='ARCHIVED' WHERE id=:id",
                    "submitted": "UPDATE telegram_intake_drafts SET status='SUBMITTED' "
                                 "WHERE id=:id",
                }
                connection.execute(text(statements[change]),
                                   {"member": member, "clinic": clinic, "id": draft["id"]})
            expected = 403 if change in {"member", "subscription"} else (
                410 if change == "expired" else 404
            )
            for response in (
                client.post(path, headers=actor_headers(actor), json=payload),
                client.get(f"/v1/telegram-intake-drafts/{draft['id']}/understanding",
                           headers=actor_headers(actor)),
            ):
                assert response.status_code == expected, response.text
                assert "sourceSpans" not in response.text
    finally:
        engine.dispose()


def test_empty_discovery_during_safe_stop_never_calls_runner_or_reserves(
    draft_owner, monkeypatch,
):
    from unittest.mock import AsyncMock

    actor, clinic, _, draft = draft_owner
    engine = create_engine(owner_database_url())
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    try:
        with application_client() as client:
            runner = AsyncMock()
            client.app.state.intake_preparation_runner = runner
            response = client.get(f"/v1/telegram-intake-drafts/{draft['id']}/understanding",
                                  headers=actor_headers(actor))
            assert response.status_code == 200 and response.json() == {"operation": None}
            runner.submit.assert_not_awaited()
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT count(*) FROM idempotency_records WHERE clinic_id=:clinic "
                "AND resource_type='INTAKE_PREPARATION'"
            ), {"clinic": clinic}) == 0
    finally:
        engine.dispose()
