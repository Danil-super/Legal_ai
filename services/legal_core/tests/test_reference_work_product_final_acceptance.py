"""A reference trace must remain live after late work-product writes and checks."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_case_api import actor_headers
from test_lawyer_draft_context import routed_case as routed_case
from test_lawyer_work_product_api import plan_path, post, submission
from test_reference_analogy_context import seed_reference
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


def expire_soon(owner, reference):
    with owner.begin() as db:
        db.execute(
            text(
                "UPDATE reference_evaluation_case_versions SET content_expires_at="
                "clock_timestamp()+interval '2 seconds' WHERE reference_case_id=:id"
            ),
            {"id": reference},
        )


@pytest.mark.parametrize("decision", ["APPROVE", "REVISE"])
def test_late_reviewer_fk_wait_expiry_rolls_back_human_version_and_receipt(
    routed_case,
    monkeypatch,
    decision,
):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    reference = seed_reference(routed_case)
    created = post(routed_case)
    assert created.status_code == 201, created.text
    client, owner = routed_case[0], routed_case[9]
    lawyer = next(iter(routed_case[10]))
    path = plan_path(routed_case)
    assert (
        client.post(path.rsplit("/", 1)[0] + "/claim", headers=actor_headers(lawyer)).status_code
        == 200
    )
    view = client.get(path, headers=actor_headers(lawyer))
    assert view.status_code == 200 and view.json()["isCurrent"], view.text
    key = uuid4()
    body = {
        "expectedVersionId": view.json()["versionId"],
        "expectedContentSha256": view.json()["contentSha256"],
        "decision": decision,
    }
    if decision == "REVISE":
        body["actions"] = ["Сохраните обращение для проверки юристом."]
    expire_soon(owner, reference)
    with owner.connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = blocker.begin()
        blocker.execute(
            text(
                "SELECT cu.id FROM clinic_users cu JOIN users u ON u.id=cu.user_id "
                "WHERE u.telegram_user_id=:lawyer FOR UPDATE OF cu"
            ),
            {"lawyer": lawyer},
        )
        future = pool.submit(
            client.post, path + "/reviews", headers=actor_headers(lawyer, key), json=body
        )
        deadline = time.monotonic() + 5
        blocked = False
        while time.monotonic() < deadline:
            with owner.connect() as db:
                blocked = bool(
                    db.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE "
                            "datname=current_database() AND wait_event_type='Lock' AND "
                            "query LIKE '%case_work_product_versions%'"
                        )
                    )
                )
            if blocked:
                break
            time.sleep(0.02)
        time.sleep(2.1)
        transaction.commit()
        response = future.result(timeout=5)
    assert blocked, response.text
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "REFERENCE_ANALOGY_CONTEXT_STALE"
    with owner.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM case_work_product_versions")) == 1
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM audit_events WHERE action='LAWYER_WORK_PRODUCT_REVIEWED'"
                )
            )
            == 0
        )
        assert (
            db.scalar(
                text("SELECT count(*) FROM idempotency_records WHERE key=:key"), {"key": str(key)}
            )
            == 0
        )


def wait_blocked(owner, fragment):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        with owner.connect() as observer:
            if observer.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname=current_database() "
                    "AND wait_event_type='Lock' AND query LIKE :pattern"
                ),
                {"pattern": "%" + fragment + "%"},
            ):
                return True
        time.sleep(0.02)
    return False


def test_private_ai_writer_expiry_after_late_verifier_risk_fk_wait(routed_case, monkeypatch):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    reference = seed_reference(routed_case)
    body = submission(routed_case)
    owner = routed_case[9]
    expire_soon(owner, reference)
    with owner.connect() as connection, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = connection.begin()
        connection.execute(
            text("SELECT id FROM case_risk_assessments WHERE case_id=:id FOR UPDATE"),
            {"id": routed_case[4]},
        )
        future = pool.submit(post, routed_case, body)
        blocked = wait_blocked(owner, "case_analysis_runs")
        time.sleep(2.1)
        transaction.commit()
        response = future.result(timeout=5)
        assert blocked, response.text
        assert response.status_code == 409, (response.status_code, response.text)
    with owner.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM case_work_product_versions")) == 0


@pytest.mark.parametrize("operation", ["lookup", "submit-replay"])
def test_private_replay_expiry_after_late_pending_audit_wait(routed_case, monkeypatch, operation):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    reference = seed_reference(routed_case)
    body = submission(routed_case)
    created = post(routed_case, body)
    assert created.status_code == 201, created.text
    owner, client = routed_case[9], routed_case[0]
    context_request = {
        "expectedPreparationOperationId": body["expectedSource"]["operationId"],
        "expectedFactSnapshotSha256": body["expectedSource"]["factSnapshotSha256"],
        "expectedRiskPolicyVersion": body["expectedSource"]["context"]["riskPolicyVersion"],
    }
    expire_soon(owner, reference)
    with owner.connect() as connection, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = connection.begin()
        connection.execute(text("LOCK TABLE audit_events IN SHARE MODE"))
        if operation == "lookup":
            future = pool.submit(
                client.post,
                f"/v1/cases/{routed_case[4]}/lawyer-work-products/lookup",
                headers=routed_case[6],
                json=context_request,
            )
        else:
            future = pool.submit(post, routed_case, body)
        blocked = wait_blocked(owner, "audit_events")
        time.sleep(2.1)
        transaction.commit()
        response = future.result(timeout=5)
        assert blocked, response.text
        assert response.status_code == 409, (response.status_code, response.text)
