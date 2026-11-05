"""Normal reports cannot accept sources withdrawn after reasoning retrieval."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import text

from legal_core import analysis_api
from test_case_api import actor_headers
from test_lawyer_draft_source_freshness import clinic_document, wait_for_block
from test_preparation_analysis import _submission
from test_preparation_analysis import prepared_case as prepared_case
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("result_version", [1, 2])
@pytest.mark.parametrize("source", ["legal", "clinic", "policy"])
def test_normal_report_rejects_withdrawn_source_after_retrieval(
    prepared_case, monkeypatch, result_version, source,
):
    client, _, _, case, headers, context, engine = prepared_case
    clinic_path, _ = clinic_document(prepared_case)
    current = client.get(f"/v1/cases/{case}/analysis-context", headers=headers)
    assert current.status_code == 200, current.text
    context = current.json()
    assert context["clinicDocumentContext"], context
    entered, release = Event(), Event()
    original = analysis_api._store_report

    async def pause(session, **kwargs):
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        return await original(session, **kwargs)

    monkeypatch.setattr(analysis_api, "_store_report", pause)
    path = "analysis-submissions" if result_version == 1 else "preparation-submissions"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f"/v1/cases/{case}/{path}", headers=headers,
                             json=_submission(context, questions=False, claims=True))
        try:
            assert entered.wait(10)
            _withdraw_source(prepared_case, clinic_path, source)
        finally:
            release.set()
        response = future.result(timeout=15)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ANALYSIS_CONTEXT_STALE"
    with engine.connect() as connection:
        for table in ("case_reports", "case_analysis_runs", "case_risk_assessments"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE case_id=:case"),
                                     {"case": case}) == 0



def _withdraw_source(prepared_case, clinic_path, source):
    client, actor = prepared_case[:2]
    engine = prepared_case[-1]
    if source == "clinic":
        response = client.post(clinic_path, headers=actor_headers(actor), json={
            "decision": "RETIRED", "reasonCode": "SYNTHETIC_WITHDRAWAL",
        })
        assert response.status_code == 201, response.text
    else:
        with engine.begin() as connection:
            if source == "legal":
                connection.execute(text("UPDATE users SET system_role=NULL "
                                        "WHERE system_role='LEGAL_EDITOR'"))
                assert connection.scalar(text(
                    "SELECT count(*) FROM production_legal_fragments")) == 0
            else:
                connection.execute(text(
                    "INSERT INTO risk_policy_events(risk_policy_id,actor_user_id,decision,"
                    "expected_content_sha256,reason_code) SELECT id,approved_by_user_id,'RETIRED',"
                    "content_sha256,'SYNTHETIC_POLICY_REVIEW' FROM risk_policy_versions "
                    "WHERE status='APPROVED'"
                ))
                connection.execute(text("UPDATE risk_policy_versions SET status='RETIRED' "
                                        "WHERE status='APPROVED'"))


@pytest.mark.parametrize("result_version", [1, 2])
@pytest.mark.parametrize("source", ["legal", "clinic", "policy"])
def test_normal_report_holds_selected_provenance_until_commit(
    prepared_case, monkeypatch, result_version, source,
):
    client, _, _, case, headers, _, engine = prepared_case
    clinic_path, _ = clinic_document(prepared_case)
    context = client.get(f"/v1/cases/{case}/analysis-context", headers=headers).json()
    entered, release = Event(), Event()
    holder = []
    original = analysis_api.require_analysis_sources_current

    async def pause(session, **kwargs):
        await original(session, **kwargs)
        holder.append(await session.scalar(text("SELECT pg_backend_pid()")))
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)

    monkeypatch.setattr(analysis_api, "require_analysis_sources_current", pause)
    path = "analysis-submissions" if result_version == 1 else "preparation-submissions"
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(client.post, f"/v1/cases/{case}/{path}", headers=headers,
                             json=_submission(context, questions=False, claims=True))
        withdrawal = None
        try:
            assert entered.wait(10)
            withdrawal = pool.submit(_withdraw_source, prepared_case, clinic_path, source)
            wait_for_block(engine, holder[0])
            assert not withdrawal.done()
        finally:
            release.set()
        response = future.result(timeout=15)
        if withdrawal is not None:
            withdrawal.result(timeout=15)
    assert response.status_code == 201, response.text
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM case_reports WHERE case_id=:case"),
                                 {"case": case}) == 1


@pytest.mark.parametrize("result_version", [1, 2])
def test_normal_report_waits_for_source_lock_then_rechecks_current_view(
    prepared_case, result_version,
):
    client, _, _, case, headers, context, engine = prepared_case
    path = "analysis-submissions" if result_version == 1 else "preparation-submissions"
    with engine.connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = blocker.begin()
        blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
        blocker.execute(text("SELECT id FROM users WHERE system_role='LEGAL_EDITOR' FOR UPDATE"))
        future = pool.submit(client.post, f"/v1/cases/{case}/{path}", headers=headers,
                             json=_submission(context, questions=False, claims=True))
        try:
            wait_for_block(engine, blocker_pid)
            blocker.execute(text("UPDATE users SET system_role=NULL "
                                 "WHERE system_role='LEGAL_EDITOR'"))
            transaction.commit()
        finally:
            if transaction.is_active:
                transaction.rollback()
        response = future.result(timeout=15)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ANALYSIS_CONTEXT_STALE"
    with engine.connect() as connection:
        for table in ("case_reports", "case_analysis_runs", "case_risk_assessments"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE case_id=:case"),
                                     {"case": case}) == 0


@pytest.mark.parametrize("result_kind", ["v1_report", "v2_report", "v2_report_free"])
@pytest.mark.parametrize("fault", ["membership_revoked", "subscription_expired"])
def test_source_lock_wait_cannot_preserve_withdrawn_or_expired_actor_access(
    prepared_case, result_kind, fault,
):
    client, actor, _, case, headers, context, engine = prepared_case
    if fault == "subscription_expired":
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE subscription_entitlements "
                "SET ends_at=clock_timestamp()+interval '3 seconds' "
                "WHERE user_id=(SELECT id FROM users WHERE telegram_user_id=:actor)"
            ), {"actor": actor})
    path = "analysis-submissions" if result_kind == "v1_report" else "preparation-submissions"
    with engine.connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = blocker.begin()
        blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
        blocker.execute(text("SELECT id FROM users WHERE system_role='LEGAL_EDITOR' FOR UPDATE"))
        future = pool.submit(client.post, f"/v1/cases/{case}/{path}", headers=headers,
                             json=_submission(context, questions=False,
                                              claims=result_kind != "v2_report_free"))
        try:
            wait_for_block(engine, blocker_pid)
            if fault == "membership_revoked":
                blocker.execute(text(
                    "UPDATE clinic_users SET status='REVOKED' "
                    "WHERE user_id=(SELECT id FROM users WHERE telegram_user_id=:actor)"
                ), {"actor": actor})
            else:
                blocker.execute(text(
                    "SELECT pg_sleep(GREATEST(0, extract(epoch FROM (ends_at-clock_timestamp())))"
                    "+0.1) FROM subscription_entitlements "
                    "WHERE user_id=(SELECT id FROM users WHERE telegram_user_id=:actor)"
                ), {"actor": actor})
            transaction.commit()
        finally:
            if transaction.is_active:
                transaction.rollback()
        response = future.result(timeout=15)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == (
        "ACTOR_NOT_AUTHORIZED" if fault == "membership_revoked" else "SUBSCRIPTION_INACTIVE"
    )
    with engine.connect() as connection:
        for table in ("case_reports", "case_analysis_runs", "case_risk_assessments"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE case_id=:case"),
                                     {"case": case}) == 0
        assert connection.scalar(text("SELECT count(*) FROM idempotency_records WHERE key=:key"),
                                 {"key": headers["Idempotency-Key"]}) == 0
