"""A completed urgent routing receipt must not prevent retrying a missing AI plan."""

import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from test_case_api import actor_headers
from test_lawyer_draft_context import routed_case as routed_case
from test_lawyer_work_product_api import post
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="isolated PostgreSQL")


@pytest.mark.parametrize("has_plan", [False, True])
@pytest.mark.parametrize("terminal,attempts", [("SUCCEEDED", 1), ("FAILED", 3)])
def test_retry_only_requeues_missing_plan_and_keeps_the_same_durable_intent(
    routed_case, has_plan, terminal, attempts,
):
    client, actor, clinic, _, case, job, *_ = routed_case
    if has_plan:
        assert post(routed_case).status_code == 201
    with routed_case[9].begin() as db:
        db.execute(text("UPDATE analysis_jobs SET state=:state,attempts=:attempts,lease_token=NULL,"
                        "lease_until=NULL,notified_at=clock_timestamp() WHERE id=:id"),
                   {"id": job.id, "state": terminal, "attempts": attempts})
    path = f"/v1/cases/{case}/analysis-jobs"
    result = client.post(path, headers=actor_headers(actor), json={"messageId": 99})
    assert result.status_code == 202, result.text
    assert result.json()["jobId"] == str(job.id)
    assert result.json()["state"] == ("SUCCEEDED" if has_plan else "QUEUED")
    duplicate = client.post(path, headers=actor_headers(actor), json={"messageId": 100})
    assert duplicate.json()["jobId"] == str(job.id)
    assert duplicate.json()["state"] == result.json()["state"]
    with routed_case[9].connect() as db:
        assert db.scalar(text("SELECT count(*) FROM analysis_jobs WHERE clinic_id=:clinic"),
                         {"clinic": clinic}) == 1
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions")) == int(has_plan)
        assert db.scalar(text("SELECT count(*) FROM case_escalations")) == 1
        if not has_plan:
            assert db.scalar(text("SELECT notified_at FROM analysis_jobs WHERE id=:id"),
                             {"id": job.id}) is None
            row = db.execute(text("SELECT attempts,message_id FROM analysis_jobs WHERE id=:id"),
                             {"id": job.id}).one()
            assert row.attempts == 0 and row.message_id == 45


def test_delayed_notification_ack_cannot_mark_a_requeued_job_delivered(routed_case):
    client, actor, _, _, _, job, *_ = routed_case
    engine = routed_case[9]
    with engine.begin() as db:
        db.execute(text("UPDATE analysis_jobs SET state='SUCCEEDED',lease_token=NULL,"
                        "lease_until=NULL,notified_at=NULL WHERE id=:id"), {"id": job.id})
    with ThreadPoolExecutor(max_workers=1) as pool, engine.connect() as lock:
        transaction = lock.begin()
        try:
            lock.execute(text("SELECT id FROM analysis_jobs WHERE id=:id FOR UPDATE"),
                         {"id": job.id})
            pending = pool.submit(client.post, f"/v1/analysis-jobs/{job.id}/notification-ack",
                                  headers=actor_headers(actor))
            deadline = time.monotonic() + 5
            while True:
                with engine.connect() as db:
                    waiting = db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE "
                        "datname=current_database() AND wait_event_type='Lock' "
                        "AND query ILIKE '%analysis_jobs%'"))
                if waiting:
                    break
                assert time.monotonic() < deadline, "ACK never reached the actual lock wait"
                time.sleep(0.01)
            lock.execute(text("UPDATE analysis_jobs SET state='QUEUED',notified_at=NULL "
                              "WHERE id=:id"), {"id": job.id})
            transaction.commit()
            assert pending.result(timeout=5).status_code == 204
        finally:
            if transaction.is_active:
                transaction.rollback()
    with engine.connect() as db:
        row = db.execute(text("SELECT state,notified_at FROM analysis_jobs WHERE id=:id"),
                         {"id": job.id}).one()
        assert row.state == "QUEUED" and row.notified_at is None


def test_retry_does_not_reopen_a_resolved_escalation(routed_case):
    client, actor, _, _, case, job, _, _, early, engine, lawyers = routed_case
    lawyer = next(iter(lawyers))
    path = f"/v1/case-escalations/{early['escalationId']}"
    assert client.post(path + "/claim", headers=actor_headers(lawyer)).status_code == 200
    assert client.post(path + "/resolve", headers=actor_headers(lawyer),
                       json={"body": "Учебная ситуация проверена."}).status_code == 200
    with engine.begin() as db:
        db.execute(text("UPDATE analysis_jobs SET state='SUCCEEDED',lease_token=NULL,"
                        "lease_until=NULL WHERE id=:id"), {"id": job.id})
    detail = client.get(path, headers=actor_headers(actor))
    assert detail.json()["canRequestAiPlan"] is False
    rejected = client.post(f"/v1/cases/{case}/analysis-jobs", headers=actor_headers(actor),
                           json={"messageId": 99})
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "ESCALATION_RESOLVED"
