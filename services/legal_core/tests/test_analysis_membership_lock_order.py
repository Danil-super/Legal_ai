"""A report's membership FK locks must not deadlock identity writers."""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core import analysis_api
from legal_core.bootstrap_admin import bootstrap_admin
from legal_core.database import database_url
from test_case_api import actor_headers
from test_legal_editor_workspace_api import _ingest, _version_payload, _write_manifest
from test_preparation_analysis import _submission
from test_preparation_analysis import prepared_case as prepared_case
from test_verified_copy_analysis_e2e import EDITOR_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


def _writer_completed_or_blocked_by_report(engine, writer, report_pid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if writer.done():
            return
        with engine.connect() as observer:
            if observer.scalar(text(
                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                "WHERE :pid=ANY(pg_blocking_pids(pid)))"
            ), {"pid": report_pid}):
                return
        time.sleep(0.01)
    pytest.fail("identity writer neither completed nor reached the report's FK lock")


@pytest.mark.parametrize("writer_kind", ["clinic_add", "platform_grant", "bootstrap"])
def test_actor_editor_report_and_identity_writer_complete_without_deadlock(
    prepared_case, tmp_path, monkeypatch, writer_kind,
):
    client, actor, clinic, case, headers, _, engine = prepared_case
    owner = actor + 100_000_000
    owner_id, owner_membership = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(text("UPDATE users SET system_role='LEGAL_EDITOR' "
                                "WHERE telegram_user_id=:actor"), {"actor": actor})
        # The target is a normal admin, so clinic-add/bootstrap perform a real role change.
        connection.execute(text("UPDATE clinic_users SET role='CLINIC_ADMIN' "
                                "WHERE user_id=(SELECT id FROM users "
                                "WHERE telegram_user_id=:actor)"), {"actor": actor})
        connection.execute(text("INSERT INTO users(id,telegram_user_id) VALUES (:id,:tg)"),
                           {"id": owner_id, "tg": owner})
        connection.execute(text(
            "INSERT INTO clinic_users(id,clinic_id,user_id,role) "
            "VALUES (:id,:clinic,:user,'CLINIC_OWNER')"
        ), {"id": owner_membership, "clinic": clinic, "user": owner_id})
        connection.execute(text(
            "INSERT INTO subscription_entitlements(clinic_id,user_id,status,plan_code,starts_at) "
            "VALUES (:clinic,:user,'ACTIVE','MVP',clock_timestamp()-interval '1 day')"
        ), {"clinic": clinic, "user": owner_id})
    monkeypatch.setenv("PLATFORM_OWNER_TELEGRAM_ID", str(owner))
    version = _ingest(_write_manifest(
        tmp_path, fragment_text="Синтетическая норма: платные медицинские услуги. "
        "Зафиксировать обращение.",
    ))
    approval = client.post(f"/v1/legal/review-queue/{version}/approval-events", headers={
        **actor_headers(actor, uuid4()), "X-Legal-Editor-Gateway-Key": EDITOR_KEY,
    }, json=_version_payload(version))
    assert approval.status_code == 200, approval.text
    context_response = client.get(f"/v1/cases/{case}/analysis-context", headers=headers)
    assert context_response.status_code == 200, context_response.text
    context = context_response.json()
    assert str(version) in {item["versionId"] for item in context["evidence"]}
    entered, release = Event(), Event()
    holder = []
    original = analysis_api._commit_analysis_result

    async def pause_commit(session, *args, **kwargs):
        await session.flush()
        holder.append(await session.scalar(text("SELECT pg_backend_pid()")))
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        return await original(session, *args, **kwargs)

    monkeypatch.setattr(analysis_api, "_commit_analysis_result", pause_commit)

    def submit_report():
        return client.post(f"/v1/cases/{case}/analysis-submissions", headers=headers,
                           json=_submission(context, questions=False, claims=True))

    def update_identity():
        if writer_kind == "clinic_add":
            return client.post("/v1/clinic/members", headers=actor_headers(owner), json={
                "telegramUserId": actor, "role": "CLINIC_LAWYER",
            })
        if writer_kind == "platform_grant":
            return client.post("/v1/platform/subscription-grants",
                               headers=actor_headers(owner, uuid4()),
                               json={"telegramUserId": actor})

        async def promote():
            runtime = create_async_engine(database_url())
            try:
                return await bootstrap_admin(async_sessionmaker(runtime), actor, "Synthetic")
            finally:
                await runtime.dispose()

        return asyncio.run(promote())

    with ThreadPoolExecutor(max_workers=2) as pool:
        report = pool.submit(submit_report)
        writer = None
        try:
            assert entered.wait(10)
            writer = pool.submit(update_identity)
            _writer_completed_or_blocked_by_report(engine, writer, holder[0])
        finally:
            release.set()
        report_response = report.result(timeout=15)
        assert writer is not None
        writer_response = writer.result(timeout=15)
    if writer_kind != "bootstrap":
        assert writer_response.status_code == 201, writer_response.text
    expected_role = {"clinic_add": "CLINIC_LAWYER", "platform_grant": "CLINIC_ADMIN",
                     "bootstrap": "CLINIC_OWNER"}[writer_kind]
    with engine.connect() as connection:
        roles = connection.scalars(text(
            "SELECT role FROM clinic_users WHERE user_id=(SELECT id FROM users "
            "WHERE telegram_user_id=:actor) AND status='ACTIVE'"
        ), {"actor": actor}).all()
        assert roles == [expected_role]
    if writer_kind == "platform_grant":
        # A current renewal preserves the exact same actor and can commit the report.
        assert report_response.status_code == 201, report_response.text
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM case_reports WHERE case_id=:case"),
                                     {"case": case}) == 1
        return
    assert report_response.status_code == 403, report_response.text
    assert report_response.json()["error"]["code"] == "ACTOR_CONTEXT_CHANGED"
    with engine.connect() as connection:
        for table in ("case_reports", "case_analysis_runs", "case_risk_assessments"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE case_id=:case"),
                                     {"case": case}) == 0

    retry = client.post(f"/v1/cases/{case}/analysis-submissions", headers=headers,
                        json=_submission(context, questions=False, claims=True))
    assert retry.status_code == 201, retry.text
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM case_reports WHERE case_id=:case"),
                                 {"case": case}) == 1
