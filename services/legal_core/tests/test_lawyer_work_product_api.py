"""Real HTTP/PG boundary: AI proposes, Core verifies, assigned human reviews."""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text

from legal_core.lawyer_work_product_contracts import LawyerWorkProductView
from legal_core import lawyer_work_products
from test_case_api import actor_headers, seed_admin
from test_lawyer_draft_context import read, routed_case as routed_case
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


def submission(fixture):
    context = read(fixture)
    assert context.status_code == 200, context.text
    body = context.json()
    fragment = body["context"]["evidence"][0]["fragmentId"]
    return {"expectedSource": body["source"], "claims": [{
        "claimId": "record-request", "kind": "ACTION",
        "text": "Зафиксируйте обращение и сохраните относящиеся к нему документы.",
        "evidenceFragmentIds": [fragment], "requiredFactKeys": ["EVENT_SUMMARY"],
    }], "semanticReviews": [{"claimId": "record-request", "verdict": "SUPPORTED",
                              "reviewedFragmentIds": [fragment]}]}


def post(fixture, body=None, headers=None):
    return fixture[0].post(f"/v1/cases/{fixture[4]}/lawyer-work-products",
                           headers=fixture[6] if headers is None else headers,
                           json=submission(fixture) if body is None else body)


def plan_path(fixture):
    return f"/v1/case-escalations/{fixture[8]['escalationId']}/work-product"


def owner_role(fixture):
    with fixture[9].begin() as db:
        db.execute(text("UPDATE clinic_users SET role='CLINIC_OWNER' WHERE id=:id"),
                   {"id": fixture[3]})


def test_verified_ai_plan_is_durable_idempotent_and_never_a_patient_report(routed_case):
    body = submission(routed_case)
    created = post(routed_case, body)
    assert created.status_code == 201, created.text
    plan = LawyerWorkProductView.model_validate(created.json())
    assert plan.review_state == "UNAPPROVED" and plan.origin == "AI" and plan.is_current
    assert plan.content.actions == [body["claims"][0]["text"]]
    assert plan.content.legal_fragment_ids == [plan.citations[0].fragment_id]
    assert "reportId" not in created.text and "analysisAllowed" not in created.text
    replay = post(routed_case, body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == created.json()
    with routed_case[9].connect() as db:
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions")) == 1
        assert db.scalar(text("SELECT count(*) FROM case_reports")) == 0
        assert db.scalar(text("SELECT status FROM cases WHERE id=:id"),
                         {"id": routed_case[4]}) == "ESCALATION_REQUIRED"
        audit = db.scalar(text("SELECT metadata_json FROM audit_events "
                               "WHERE action='LAWYER_WORK_PRODUCT_CREATED'"))
        assert body["claims"][0]["text"] not in json.dumps(audit, ensure_ascii=False)
        assert db.scalar(text("SELECT state FROM analysis_jobs WHERE id=:id"),
                         {"id": routed_case[5].id}) == "RUNNING"


@pytest.mark.parametrize("fault", ["unsupported", "no-review", "foreign-evidence", "empty",
                                   "source-hash", "expired-lease", "wrong-key"])
def test_ai_writer_fails_closed_without_a_plan_or_report(routed_case, fault):
    body = submission(routed_case)
    headers = dict(routed_case[6])
    if fault == "unsupported":
        body["semanticReviews"][0]["verdict"] = "UNSUPPORTED"
    elif fault == "no-review":
        body["semanticReviews"] = []
    elif fault == "foreign-evidence":
        body["claims"][0]["evidenceFragmentIds"] = [str(uuid4())]
    elif fault == "empty":
        body["claims"] = body["semanticReviews"] = []
    elif fault == "source-hash":
        body["expectedSource"]["context"]["evidenceTraceSha256"] = "0" * 64
    elif fault == "wrong-key":
        headers["X-Agent-Internal-Key"] = "x" * 40
    else:
        with routed_case[9].begin() as db:
            db.execute(text("UPDATE analysis_jobs SET lease_until=clock_timestamp()"
                            "-interval '1 second' WHERE id=:id"), {"id": routed_case[5].id})
    denied = post(routed_case, body, headers)
    assert denied.status_code in {403, 409, 422}, denied.text
    with routed_case[9].connect() as db:
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions")) == 0
        assert db.scalar(text("SELECT count(*) FROM case_reports")) == 0


def test_assigned_human_revises_then_approves_without_overwriting_ai_or_closing_case(routed_case):
    created = post(routed_case)
    assert created.status_code == 201, created.text
    client, actor, *_ = routed_case
    lawyer = next(iter(routed_case[10]))
    other = next(item for item in routed_case[10] if item != lawyer)
    path = plan_path(routed_case)
    # Until approval the clinic creator and an unassigned lawyer cannot read it.
    assert client.get(path, headers=actor_headers(actor)).status_code == 404
    assert client.get(path, headers=actor_headers(lawyer)).status_code == 404
    owner_role(routed_case)
    assert client.get(path, headers=actor_headers(actor)).status_code == 200
    assert client.post(path.rsplit("/", 1)[0] + "/claim",
                       headers=actor_headers(lawyer)).status_code == 200
    assert client.get(path, headers=actor_headers(other)).status_code == 404
    view = client.get(path, headers=actor_headers(lawyer))
    assert view.status_code == 200, view.text
    edit = {"expectedVersionId": view.json()["versionId"],
            "expectedContentSha256": view.json()["contentSha256"], "decision": "REVISE",
            "actions": ["Сохраните обращение и подготовьте хронологию для проверки юристом."]}
    key = uuid4()
    revised = client.post(path + "/reviews", headers=actor_headers(lawyer, key), json=edit)
    assert revised.status_code == 201, revised.text
    assert revised.json()["reviewState"] == "HUMAN_REVISED"
    assert revised.json()["content"]["version"] == 2
    assert revised.json()["content"]["parentVersion"] == 1
    assert client.post(path + "/reviews", headers=actor_headers(lawyer, key),
                       json=edit).json() == revised.json()
    approve = {"expectedVersionId": revised.json()["versionId"],
               "expectedContentSha256": revised.json()["contentSha256"], "decision": "APPROVE"}
    approved = client.post(path + "/reviews", headers=actor_headers(lawyer, uuid4()), json=approve)
    assert approved.status_code == 201, approved.text
    assert approved.json()["reviewState"] == "HUMAN_APPROVED"
    assert approved.json()["content"]["version"] == 3
    # A new stale click cannot approve/overwrite the prior version.
    assert client.post(path + "/reviews", headers=actor_headers(lawyer, uuid4()),
                       json=approve).status_code == 409
    with routed_case[9].connect() as db:
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions")) == 3
        assert db.scalar(text("SELECT count(*) FROM case_reports")) == 0
    # Creator gets a human-approved plan, never another clinic's user.
    with routed_case[9].begin() as db:
        db.execute(text("UPDATE clinic_users SET role='CLINIC_ADMIN' WHERE id=:id"),
                   {"id": routed_case[3]})
    assert client.get(path, headers=actor_headers(actor)).status_code == 200
    foreign = actor + 200_000_000
    seed_admin(foreign)
    assert client.get(path, headers=actor_headers(foreign)).status_code == 404


def test_fact_change_marks_plan_stale_and_cannot_be_approved(routed_case):
    assert post(routed_case).status_code == 201
    owner_role(routed_case)
    client, actor = routed_case[:2]
    path = plan_path(routed_case)
    assert client.post(path.rsplit("/", 1)[0] + "/claim",
                       headers=actor_headers(actor)).status_code == 200
    old = client.get(path, headers=actor_headers(actor)).json()
    with routed_case[9].begin() as db:
        db.execute(text(
            "INSERT INTO case_facts(clinic_id,case_id,fact_key,revision,value_type,value_json,"
            "source_type,evidence_status,recorded_by_membership_id) "
            "SELECT clinic_id,case_id,fact_key,revision+1,value_type,"
            "jsonb_build_object('text',CAST(:summary AS text)),source_type,evidence_status,"
            "recorded_by_membership_id FROM case_facts "
            "WHERE case_id=:case AND fact_key='EVENT_SUMMARY' ORDER BY revision DESC LIMIT 1"
        ), {"case": routed_case[4], "summary": "Дополнительная синтетическая информация."})
    stale = client.get(path, headers=actor_headers(actor))
    assert stale.status_code == 200 and not stale.json()["isCurrent"], stale.text
    denied = client.post(path + "/reviews", headers=actor_headers(actor, uuid4()), json={
        "expectedVersionId": old["versionId"], "expectedContentSha256": old["contentSha256"],
        "decision": "APPROVE",
    })
    assert denied.status_code == 409, denied.text


def test_private_lookup_avoids_repeating_models_and_requires_the_live_creator_lease(routed_case):
    client, actor, _, _, case, _, headers, request, *_ = routed_case
    path = f"/v1/cases/{case}/lawyer-work-products/lookup"
    empty = client.post(path, headers=headers, json=request)
    assert empty.status_code == 200 and empty.json() is None, empty.text
    created = post(routed_case)
    assert created.status_code == 201, created.text
    found = client.post(path, headers=headers, json=request)
    assert found.status_code == 200 and found.json() == created.json(), found.text
    assert client.post(path, headers=actor_headers(actor), json=request).status_code == 403
    with routed_case[9].begin() as db:
        db.execute(text("UPDATE analysis_jobs SET lease_until=clock_timestamp()"
                        "-interval '1 second' WHERE id=:id"), {"id": routed_case[5].id})
    assert client.post(path, headers=headers, json=request).status_code == 409


@pytest.mark.parametrize("who", ["creator", "owner-unassigned", "lawyer-unassigned", "foreign"])
def test_review_cannot_be_performed_by_unassigned_or_foreign_actor(routed_case, who):
    created = post(routed_case)
    assert created.status_code == 201, created.text
    actor = routed_case[1]
    if who == "owner-unassigned":
        owner_role(routed_case)
    elif who == "lawyer-unassigned":
        actor = next(iter(routed_case[10]))
    elif who == "foreign":
        actor += 200_000_000
        seed_admin(actor, role="CLINIC_OWNER")
    denied = routed_case[0].post(plan_path(routed_case) + "/reviews",
                                headers=actor_headers(actor, uuid4()), json={
        "expectedVersionId": created.json()["versionId"],
        "expectedContentSha256": created.json()["contentSha256"], "decision": "APPROVE",
    })
    assert denied.status_code == (404 if who == "foreign" else 403), denied.text
    with routed_case[9].connect() as db:
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions")) == 1


def test_same_job_key_with_different_claims_is_rejected_not_duplicated(routed_case):
    body = submission(routed_case)
    assert post(routed_case, body).status_code == 201
    body["claims"][0]["text"] = "Сохраните синтетическое обращение клиники для проверки."
    denied = post(routed_case, body)
    assert denied.status_code == 422 and denied.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    with routed_case[9].connect() as db:
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions")) == 1


@pytest.mark.parametrize("withdrawal", ["membership", "lease", "safe-stop"])
def test_ai_writer_rechecks_after_actual_persistence_before_commit(
    routed_case, monkeypatch, withdrawal,
):
    body = submission(routed_case)
    if withdrawal == "lease":
        with routed_case[9].begin() as db:
            db.execute(text("UPDATE analysis_jobs SET lease_until=clock_timestamp()"
                            "+interval '2 seconds' WHERE id=:id"), {"id": routed_case[5].id})
    entered, release = threading.Event(), threading.Event()
    real_view = lawyer_work_products._view

    async def pause_after_real_view(*args, **kwargs):
        result = await real_view(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(lawyer_work_products, "_view", pause_after_real_view)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(post, routed_case, body)
        try:
            assert entered.wait(5)
            if withdrawal == "safe-stop":
                monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
            elif withdrawal == "membership":
                with routed_case[9].begin() as db:
                    db.execute(text("UPDATE clinic_users SET status='REVOKED' WHERE id=:id"),
                               {"id": routed_case[3]})
            else:
                # The real locked lease expires by wall clock, without mutating its row.
                time.sleep(2.1)
        finally:
            release.set()
        denied = future.result(timeout=8)
    assert denied.status_code in {403, 409, 503}, denied.text
    with routed_case[9].connect() as db:
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions")) == 0
        assert db.scalar(text("SELECT count(*) FROM audit_events "
                               "WHERE action='LAWYER_WORK_PRODUCT_CREATED'")) == 0


def test_writer_locks_case_before_version_table_so_real_retention_cannot_deadlock(routed_case):
    body = submission(routed_case)
    with routed_case[9].connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = blocker.begin()
        try:
            blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
            blocker.execute(text("SELECT id FROM cases WHERE id=:id FOR UPDATE"),
                            {"id": routed_case[4]})
            blocker.execute(text("UPDATE cases SET retention_due_at=clock_timestamp()"
                                 "-interval '1 second' WHERE id=:id"), {"id": routed_case[4]})
            future = pool.submit(post, routed_case, body)
            deadline = time.monotonic() + 5
            waiting = None
            while waiting is None:
                waiting = blocker.scalar(text(
                    "SELECT pid FROM pg_stat_activity WHERE :blocker=ANY(pg_blocking_pids(pid))"
                ), {"blocker": blocker_pid})
                assert not future.done(), "writer bypassed the locked case"
                assert time.monotonic() < deadline, "writer never reached the case lock"
                time.sleep(0.01)
            assert not blocker.scalar(text(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid AND relation="
                "'case_work_product_versions'::regclass AND mode='RowExclusiveLock')"
            ), {"pid": waiting}), "writer locked version table before Case"
            blocker.execute(text("SET LOCAL lock_timeout='2s'"))
            assert blocker.scalar(text("SELECT public.purge_expired_case_content()")) == 1
            transaction.commit()
            denied = future.result(timeout=6)
            assert denied.status_code == 410, denied.text
        finally:
            if transaction.is_active:
                transaction.rollback()
    with routed_case[9].connect() as db:
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions")) == 0
