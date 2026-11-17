"""Actual SQL claim budgets and unchanged token/attempt fences, using a disposable database."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from legal_core.analysis_jobs import claim_job, require_job_lease
from legal_core.case_api import ApiError, resolve_actor
from legal_core.database import create_engine as async_engine
from legal_core.database import create_session_factory, owner_database_url
from test_analysis_jobs import seed_confirmed_case
from test_case_api import actor_headers, application_client
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                               reason="requires disposable PostgreSQL")
ROOT = Path(__file__).parents[3]


def queued_job(actor):
    case_id = seed_confirmed_case(actor)
    with application_client() as client:
        response = client.post(f"/v1/cases/{case_id}/analysis-jobs",
                               headers=actor_headers(actor), json={"messageId": 136})
        assert response.status_code == 202, response.text
    return response.json()["jobId"]


def test_actual_claim_budget_keeps_three_attempts_and_rotates_fenced_tokens(isolated_database):
    actor_id = 9_136_000_001
    job_id = queued_job(actor_id)

    async def run():
        engine = async_engine()
        owner = create_engine(owner_database_url())
        sessions = create_session_factory(engine)
        old_token = None
        try:
            for attempt in range(1, 4):
                job = await claim_job(sessions)
                assert job is not None and str(job.id) == job_id
                with owner.begin() as db:
                    row = db.execute(text(
                        "SELECT attempts,extract(epoch FROM (lease_until-updated_at)) "
                        "FROM analysis_jobs WHERE id=:id"
                    ), {"id": job.id}).one()
                assert row == (attempt, 300)
                async with sessions() as session:
                    actor = await resolve_actor(session, actor_id)
                    await require_job_lease(session, actor, job.case_id, job.id, job.lease_token)
                    if old_token is not None:
                        assert job.lease_token != old_token
                        with pytest.raises(ApiError, match="lease"):
                            await require_job_lease(session, actor, job.case_id, job.id, old_token)
                    with pytest.raises(ApiError, match="lease"):
                        await require_job_lease(session, actor, job.case_id, job.id, uuid4())
                old_token = job.lease_token
                with owner.begin() as db:
                    db.execute(text(
                        "UPDATE analysis_jobs SET lease_until=now()-interval '1 second' "
                        "WHERE id=:id"
                    ), {"id": job.id})
            assert await claim_job(sessions) is None
            with owner.connect() as db:
                assert db.execute(text(
                    "SELECT state,attempts,error_code,lease_token,lease_until "
                    "FROM analysis_jobs WHERE id=:id"
                ), {"id": job_id}).one() == ("FAILED", 3, "ANALYSIS_INTERRUPTED", None, None)
        finally:
            await engine.dispose()
            owner.dispose()

    asyncio.run(run())


def test_claim_budget_migration_preserves_function_identity_privileges_and_existing_lease(
    isolated_database,
):
    owner = create_engine(owner_database_url())
    config = Config(str(ROOT / "alembic.ini"))

    def function_metadata():
        with owner.connect() as db:
            return db.execute(text(
                "SELECT oid,proowner,prosecdef,proconfig,proacl FROM pg_proc "
                "WHERE oid='public.claim_analysis_job()'::regprocedure"
            )).one()

    async def claim_one():
        engine = async_engine()
        try:
            return await claim_job(create_session_factory(engine))
        finally:
            await engine.dispose()

    def duration(job_id):
        with owner.connect() as db:
            return db.scalar(text("SELECT extract(epoch FROM (lease_until-updated_at)) "
                                  "FROM analysis_jobs WHERE id=:id"), {"id": job_id})

    try:
        metadata = function_metadata()
        queued_job(9_136_000_002)
        retained = asyncio.run(claim_one())
        assert retained is not None and duration(retained.id) == 300
        command.downgrade(config, "b64f1a8c2d53")
        assert function_metadata() == metadata
        assert duration(retained.id) == 300  # Replacing the function cannot rewrite an owned lease.
        queued_job(9_136_000_003)
        old = asyncio.run(claim_one())
        assert old is not None and duration(old.id) == 180
        command.upgrade(config, "head")
        assert function_metadata() == metadata
        queued_job(9_136_000_004)
        new = asyncio.run(claim_one())
        assert new is not None and duration(new.id) == 300
    finally:
        owner.dispose()
