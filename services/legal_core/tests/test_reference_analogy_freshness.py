"""Source-producer freshness, independent of the plan viewer's workspace grant."""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.case_api import ApiError, resolve_actor
from legal_core.contracts import FactKey
from legal_core.database import database_url
from legal_core.models import Case
from legal_core.reference_analogy_freshness import require_reference_analogy_trace_current
from test_case_api import actor_headers, application_client
from test_reference_analogy_retrieval import select, workspace as workspace
from test_reference_evaluation_api import _add_member
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
FACTS = {FactKey.EVENT_SUMMARY: "Пломба выпала"}


def case_for(actor):
    with application_client() as client:
        response = client.post("/v1/cases", headers=actor_headers(actor, uuid4()),
            json={"intakeSchemaVersion": "dental-case-intake.v1", "channel": "TELEGRAM"})
        assert response.status_code == 201, response.text
        return UUID(response.json()["id"])


def guard(actor, case_id, trace):
    async def invoke():
        engine = create_async_engine(database_url())
        try:
            async with async_sessionmaker(engine)() as session:
                await resolve_actor(session, actor)
                case = await session.get(Case, case_id)
                await require_reference_analogy_trace_current(
                    session, case=case, facts=FACTS, expected_trace=trace)
                await session.commit()
        finally:
            await engine.dispose()
    return asyncio.run(invoke())


def test_source_validation_uses_real_case_creator_not_the_ungranted_viewer(workspace):
    actor, clinic, _, _, create, _ = workspace
    reference = create()
    case = case_for(actor)
    trace = select(actor).trace_sha256
    viewer = actor + 2_000_000_000
    _add_member(clinic, viewer)
    assert guard(viewer, case, trace) is None
    with application_client() as client:
        assert client.get(f"/v1/reference-evaluations/{reference}",
                          headers=actor_headers(viewer)).status_code == 403


@pytest.mark.parametrize("fault", ["grant", "version", "retired", "trace", "text"])
def test_producer_or_selected_reference_change_cannot_validate_a_stored_plan(workspace, fault):
    actor, _, member, _, create, owner = workspace
    reference = create()
    case = case_for(actor)
    trace = select(actor).trace_sha256
    with owner.begin() as connection:
        if fault == "grant":
            connection.execute(text("DELETE FROM reference_evaluation_access_grants "
                                    "WHERE membership_id=:id"), {"id": member})
        elif fault in {"version", "retired"}:
            statement = ("current_version=2" if fault == "version" else "status='RETIRED'")
            connection.execute(text("UPDATE reference_evaluation_cases SET " + statement
                                    + " WHERE id=:id"), {"id": reference})
        elif fault == "text":
            connection.execute(text("UPDATE reference_evaluation_case_versions "
                                    "SET scenario_text='Пломба — измененный синтетический текст.' "
                                    "WHERE reference_case_id=:id"), {"id": reference})
        else:
            trace = "f" * 64
    with pytest.raises(ApiError) as caught:
        guard(actor, case, trace)
    assert caught.value.code == "REFERENCE_ANALOGY_CONTEXT_STALE"


@pytest.mark.parametrize("change", ["retirement", "expiration"])
def test_real_source_lock_wait_rechecks_retirement_before_accepting_trace(workspace, change):
    actor, _, _, _, create, owner = workspace
    reference = create()
    case = case_for(actor)
    trace = select(actor).trace_sha256
    with owner.connect() as connection, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = connection.begin()
        connection.execute(text("SELECT id FROM reference_evaluation_cases "
                                "WHERE id=:id FOR UPDATE"),
                           {"id": reference})
        future = pool.submit(guard, actor, case, trace)
        deadline = time.monotonic() + 5
        blocked = False
        while time.monotonic() < deadline:
            with owner.connect() as observer:
                blocked = bool(observer.scalar(text("SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type='Lock' AND query LIKE '%reference_evaluation_cases%'")))
            if blocked:
                break
            time.sleep(0.02)
        if change == "retirement":
            connection.execute(text("UPDATE reference_evaluation_cases SET status='RETIRED' "
                                    "WHERE id=:id"), {"id": reference})
        else:
            connection.execute(text("UPDATE reference_evaluation_case_versions SET "
                                    "content_expires_at=clock_timestamp()-interval '1 millisecond' "
                                    "WHERE reference_case_id=:id"), {"id": reference})
        transaction.commit()
        assert blocked, "actual selected-source lock wait was not observed"
        with pytest.raises(ApiError) as caught:
            future.result(timeout=5)
        assert caught.value.code == "REFERENCE_ANALOGY_CONTEXT_STALE"


@pytest.mark.parametrize("withdraw", [False, True])
def test_producer_guard_matches_administration_user_then_membership_lock_order(workspace, withdraw):
    actor, _, membership, _, create, owner = workspace
    create()
    case = case_for(actor)
    trace = select(actor).trace_sha256
    with owner.connect() as admin, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = admin.begin()
        admin.execute(text("SELECT id FROM users WHERE telegram_user_id=:actor FOR UPDATE"),
                      {"actor": actor})
        future = pool.submit(guard, actor, case, trace)
        deadline = time.monotonic() + 5
        blocked = False
        while time.monotonic() < deadline:
            with owner.connect() as observer:
                blocked = bool(observer.scalar(text("SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname=current_database() AND wait_event_type='Lock' "
                    "AND query LIKE '%users.id%FOR SHARE%'")))
            if blocked:
                break
            time.sleep(0.02)
        assert blocked, "the producer User lock wait was not observed"
        # The real administration order must be able to finish without deadlock.
        admin.execute(text("SELECT id FROM clinic_users WHERE id=:id FOR UPDATE"),
                      {"id": membership})
        if withdraw:
            admin.execute(text("UPDATE clinic_users SET status='REVOKED' WHERE id=:id"),
                          {"id": membership})
        transaction.commit()
        if withdraw:
            with pytest.raises(ApiError) as caught:
                future.result(timeout=5)
            assert caught.value.code == "REFERENCE_ANALOGY_CONTEXT_STALE"
        else:
            assert future.result(timeout=5) is None
