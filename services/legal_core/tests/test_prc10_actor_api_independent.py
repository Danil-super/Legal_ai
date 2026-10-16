"""Independent public discovery/consent adversarial checks; synthetic data only."""

import asyncio
import os

import httpx
import pytest
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from legal_core.main import create_app
from test_case_api import actor_headers
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
