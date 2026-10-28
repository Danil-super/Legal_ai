"""Review consent binds the displayed source, not whichever revision is current later."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from legal_core.case_api import _canonical_hash
from legal_core.reference_evaluation_contracts import (
    ReferenceEvaluationCreateResponse,
    ReferenceEvaluationReviewRequest,
)
from test_case_api import actor_headers, application_client, seed_admin
from test_reference_analogy_retrieval import workspace as workspace
from test_reference_evaluation_api import FakeReferenceEvaluationStore, _grant
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

postgres = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                              reason="requires disposable PostgreSQL")


def test_absent_review_snapshot_preserves_existing_wire_and_hash():
    request = ReferenceEvaluationReviewRequest(decision="APPROVE_FOR_EVALUATION")
    assert request.model_dump(by_alias=True) == {
        "decision": "APPROVE_FOR_EVALUATION", "note": None,
    }
    bound = ReferenceEvaluationReviewRequest.model_validate({
        "decision": "APPROVE_FOR_EVALUATION", "expectedReviewSnapshotSha256": "a" * 64,
    })
    assert bound.model_dump(by_alias=True)["expectedReviewSnapshotSha256"] == "a" * 64


def test_retained_legacy_creation_receipt_without_snapshot_remains_readable():
    wire = {"case": {
        "id": str(uuid4()), "displayName": "Синтетический эталон", "groupKey": "healthcare",
        "status": "DRAFT", "currentVersion": 1, "hasMaterial": False,
        "createdAt": "2026-10-08T00:00:00Z", "updatedAt": "2026-10-08T00:00:00Z",
        "asOfDate": "2026-10-08", "expectedRoute": "ABSTAIN",
        "scenarioText": "Синтетический пример для проверки сохранённой квитанции.",
        "contentPurgedAt": None, "createdBySelf": True,
    }}
    retained = ReferenceEvaluationCreateResponse.model_validate(wire)
    assert retained.case.review_snapshot_sha256 is None
    assert retained.model_dump(mode="json", by_alias=True) == wire


@pytest.mark.parametrize("token", ["a" * 63, "a" * 65, "A" * 64, "g" * 64, 123, ""])
def test_review_snapshot_rejects_noncanonical_hash(token):
    with pytest.raises(ValidationError):
        ReferenceEvaluationReviewRequest.model_validate({
            "decision": "APPROVE_FOR_EVALUATION", "expectedReviewSnapshotSha256": token,
        })


def _reviewer(workspace):
    with workspace[5].connect() as connection:
        return connection.scalar(text(
            "SELECT u.telegram_user_id FROM users u JOIN clinic_users c ON c.user_id=u.id "
            "WHERE c.id=:id"), {"id": workspace[3]})


def _ready(workspace, client):
    reference = workspace[4](approve=False)
    base = f"/v1/reference-evaluations/{reference}"
    submitted = client.post(base + "/submit", headers=actor_headers(workspace[0], uuid4()))
    assert submitted.status_code == 200, submitted.text
    viewed = client.get(base, headers=actor_headers(_reviewer(workspace)))
    assert viewed.status_code == 200, viewed.text
    return reference, base, viewed.json()["reviewSnapshotSha256"]


def _counts(workspace, reference):
    with workspace[5].connect() as connection:
        return tuple(connection.scalar(text(query), {"id": reference}) for query in (
            "SELECT count(*) FROM reference_evaluation_review_events WHERE reference_case_id=:id",
            "SELECT count(*) FROM audit_events WHERE resource_id=:id "
            "AND action='REFERENCE_EVALUATION_REVIEWED'",
        ))


@postgres
def test_old_displayed_revision_cannot_approve_new_unseen_ready_revision(workspace, monkeypatch):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    reviewer = _reviewer(workspace)
    with application_client() as client:
        reference, base, first = _ready(workspace, client)
        changes_headers = actor_headers(reviewer, uuid4())
        changes_body = {
            "decision": "CHANGES_REQUIRED", "note": "Уточните синтетическую фабулу.",
            "expectedReviewSnapshotSha256": first,
        }
        changes = client.post(base + "/review", headers=changes_headers, json=changes_body)
        assert changes.status_code == 200, changes.text
        revised = client.post(base + "/revisions", headers=actor_headers(workspace[0], uuid4()),
            json={"asOfDate": "2026-10-08", "expectedRoute": "ABSTAIN",
                  "scenarioText": "Новый вымышленный материал: пломба выпала после осмотра."})
        assert revised.status_code == 200, revised.text
        assert client.post(base + "/submit",
                           headers=actor_headers(workspace[0], uuid4())).status_code == 200
        before = _counts(workspace, reference)
        result = client.post(base + "/review", headers=actor_headers(reviewer, uuid4()), json={
            "decision": "APPROVE_FOR_EVALUATION", "expectedReviewSnapshotSha256": first,
        })
        assert result.status_code == 409, result.text
        assert result.json()["error"]["code"] == "REFERENCE_REVIEW_SOURCE_STALE"
        assert _counts(workspace, reference) == before
        current = client.get(base, headers=actor_headers(reviewer)).json()
        assert current["status"] == "READY_FOR_REVIEW" and current["currentVersion"] == 2
        assert current["reviewSnapshotSha256"] != first
        replay = client.post(base + "/review", headers=changes_headers, json=changes_body)
        assert replay.status_code == 409, replay.text
        assert _counts(workspace, reference) == before


@postgres
def test_same_version_uploaded_material_invalidates_displayed_snapshot(workspace, monkeypatch):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    reference = workspace[4](approve=False)
    base = f"/v1/reference-evaluations/{reference}"
    reviewer = _reviewer(workspace)
    with application_client(reference_evaluation_store=FakeReferenceEvaluationStore()) as client:
        token = client.get(base, headers=actor_headers(reviewer)).json()["reviewSnapshotSha256"]
        upload = client.post(base + "/material", headers={
            **actor_headers(workspace[0], uuid4()), "X-Source-Filename": "synthetic.txt",
            "Content-Type": "text/plain",
        }, content=b"Synthetic anonymous source: a filling requires review.")
        assert upload.status_code == 201, upload.text
        assert client.post(base + "/submit",
                           headers=actor_headers(workspace[0], uuid4())).status_code == 200
        before = _counts(workspace, reference)
        denied = client.post(base + "/review", headers=actor_headers(reviewer, uuid4()), json={
            "decision": "APPROVE_FOR_EVALUATION", "expectedReviewSnapshotSha256": token,
        })
        assert denied.status_code == 409, denied.text
        assert _counts(workspace, reference) == before
        current = client.get(base, headers=actor_headers(reviewer)).json()
        assert current["currentVersion"] == 1 and current["reviewSnapshotSha256"] != token


@postgres
@pytest.mark.parametrize("field,value", [
    ("raw_sha256", "b" * 64), ("raw_size_bytes", 60), ("raw_mime_type", "application/pdf"),
    ("scenario_text", "Новый вымышленный текст: другая фабула без изменения сохранённого хеша."),
    ("as_of_date", "2026-10-09"), ("expected_route", "HUMAN_ESCALATION"),
    ("group_key", "general"),
])
def test_same_version_replaced_raw_or_review_metadata_requires_new_consent(workspace, monkeypatch,
                                                                          field, value):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "0")
    reviewer = _reviewer(workspace)
    with application_client() as client:
        reference, base, _ = _ready(workspace, client)
        # Simulate an existing attached package and an import/repair changing the same version.
        # These are synthetic bytes' metadata; no object is read or sent to a model here.
        with workspace[5].begin() as connection:
            connection.execute(text("UPDATE reference_evaluation_case_versions SET "
                "raw_object_key=:key,raw_sha256=:hash,raw_size_bytes=50,raw_mime_type='text/plain' "
                "WHERE reference_case_id=:id"),
                {"key": f"synthetic/{reference}/material", "hash": "a" * 64, "id": reference})
        token = client.get(base, headers=actor_headers(reviewer)).json()["reviewSnapshotSha256"]
        table = "reference_evaluation_cases" if field == "group_key" else (
            "reference_evaluation_case_versions")
        key = "id" if field == "group_key" else "reference_case_id"
        with workspace[5].begin() as connection:
            connection.execute(text(f"UPDATE {table} SET {field}=:value WHERE {key}=:id"),
                               {"value": value, "id": reference})
        result = client.post(base + "/review", headers=actor_headers(reviewer, uuid4()), json={
            "decision": "APPROVE_FOR_EVALUATION", "expectedReviewSnapshotSha256": token,
        })
        assert result.status_code == 409, result.text
        assert _counts(workspace, reference) == (0, 0)
        current = client.get(base, headers=actor_headers(reviewer)).json()
        assert current["currentVersion"] == 1 and current["reviewSnapshotSha256"] != token


@postgres
def test_exact_source_approval_lost_reply_replays_after_status_and_expiry_change(workspace,
                                                                              monkeypatch):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    reviewer = _reviewer(workspace)
    with application_client() as client:
        reference, base, token = _ready(workspace, client)
        body = {"decision": "APPROVE_FOR_EVALUATION", "expectedReviewSnapshotSha256": token}
        headers = actor_headers(reviewer, uuid4())
        first = client.post(base + "/review", headers=headers, json=body)
        assert first.status_code == 200, first.text
        repeated = client.post(base + "/review", headers=headers, json=body)
        assert repeated.status_code == 200 and repeated.json() == first.json(), repeated.text
        current = client.get(base, headers=actor_headers(reviewer)).json()
        assert current["reviewSnapshotSha256"] == token
        assert _counts(workspace, reference) == (1, 1)
        with workspace[5].connect() as connection:
            metadata = connection.scalar(text("SELECT metadata_json FROM audit_events "
                "WHERE resource_id=:id AND action='REFERENCE_EVALUATION_REVIEWED'"),
                {"id": reference})
        assert metadata["reviewSnapshotSha256"] == token


@postgres
@pytest.mark.parametrize("enabled", ["0", "1"])
def test_approval_always_requires_snapshot_even_before_enabling_reference_producer(workspace,
                                                                                monkeypatch,
                                                                                enabled):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", enabled)
    with application_client() as client:
        reference, base, _ = _ready(workspace, client)
        key = uuid4()
        result = client.post(base + "/review", headers=actor_headers(_reviewer(workspace), key),
                             json={"decision": "APPROVE_FOR_EVALUATION"})
        assert result.status_code == 409, result.text
        assert _counts(workspace, reference) == (0, 0)


@postgres
@pytest.mark.parametrize("enabled", ["0", "1"])
def test_unbound_legacy_changes_request_retains_hash_only_with_feature_off(workspace, monkeypatch,
                                                                         enabled):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", enabled)
    with application_client() as client:
        reference, base, _ = _ready(workspace, client)
        key, note = uuid4(), "Уточните синтетическую фабулу."
        body = {"decision": "CHANGES_REQUIRED", "note": note}
        headers = actor_headers(_reviewer(workspace), key)
        result = client.post(base + "/review", headers=headers, json=body)
        if enabled == "1":
            assert result.status_code == 409, result.text
            assert _counts(workspace, reference) == (0, 0)
        else:
            assert result.status_code == 200, result.text
            repeated = client.post(base + "/review", headers=headers, json=body)
            assert repeated.status_code == 200 and repeated.json() == result.json()
            with workspace[5].connect() as connection:
                digest = connection.scalar(text("SELECT request_sha256 FROM idempotency_records "
                    "WHERE key=:key AND scope=:scope"),
                    {"key": str(key), "scope": f"reference-evaluations:review:{reference}"})
            assert digest == _canonical_hash({"caseId": str(reference),
                                              "decision": "CHANGES_REQUIRED", "note": note})


@postgres
def test_supplied_snapshot_is_checked_even_when_producer_disabled(workspace, monkeypatch):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "0")
    with application_client() as client:
        reference, base, _ = _ready(workspace, client)
        result = client.post(base + "/review", headers=actor_headers(_reviewer(workspace), uuid4()),
            json={"decision": "APPROVE_FOR_EVALUATION", "expectedReviewSnapshotSha256": "f" * 64})
        assert result.status_code == 409, result.text
        assert _counts(workspace, reference) == (0, 0)


@postgres
@pytest.mark.parametrize("fault", ["foreign", "withdrawn", "self", "expired"])
def test_snapshot_never_substitutes_for_scope_independence_or_live_source(workspace, monkeypatch,
                                                                       fault):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    reviewer = _reviewer(workspace)
    with application_client() as client:
        reference, base, token = _ready(workspace, client)
        if fault == "foreign":
            reviewer = workspace[0] + 3_000_000_000
            clinic, membership = seed_admin(reviewer, role="CLINIC_OWNER")
            _grant(clinic, membership, "REVIEWER")
        elif fault == "self":
            reviewer = workspace[0]
            _grant(workspace[1], workspace[2], "REVIEWER")
        else:
            with workspace[5].begin() as connection:
                if fault == "withdrawn":
                    connection.execute(text("DELETE FROM reference_evaluation_access_grants "
                                            "WHERE membership_id=:id"), {"id": workspace[3]})
                else:
                    connection.execute(text("UPDATE reference_evaluation_case_versions SET "
                        "content_expires_at=clock_timestamp()-interval '1 second' "
                        "WHERE reference_case_id=:id"), {"id": reference})
        result = client.post(base + "/review", headers=actor_headers(reviewer, uuid4()), json={
            "decision": "APPROVE_FOR_EVALUATION", "expectedReviewSnapshotSha256": token,
        })
        assert result.status_code == {"foreign": 404, "withdrawn": 403,
                                      "self": 403, "expired": 410}[fault], result.text
        assert _counts(workspace, reference) == (0, 0)


@postgres
@pytest.mark.parametrize("changed", ["source", "grant"])
def test_waiting_review_rechecks_locked_current_source_and_reviewer_grant(workspace, monkeypatch,
                                                                        changed):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    with application_client() as client:
        reference, base, token = _ready(workspace, client)
        with workspace[5].connect() as holder, ThreadPoolExecutor(max_workers=1) as pool:
            transaction = holder.begin()
            holder.execute(text("SELECT id FROM reference_evaluation_cases "
                                "WHERE id=:id FOR UPDATE"), {"id": reference})
            pending = pool.submit(client.post, base + "/review",
                headers=actor_headers(_reviewer(workspace), uuid4()), json={
                    "decision": "APPROVE_FOR_EVALUATION", "expectedReviewSnapshotSha256": token,
                })
            blocked = False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with workspace[5].connect() as observer:
                    blocked = bool(observer.scalar(text("SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname=current_database() AND wait_event_type='Lock' "
                        "AND query LIKE '%reference_evaluation_cases%FOR UPDATE%'")))
                if blocked:
                    break
                time.sleep(.02)
            if changed == "source":
                holder.execute(text("UPDATE reference_evaluation_case_versions "
                    "SET scenario_text=:source WHERE reference_case_id=:id"),
                    {"id": reference,
                     "source": "Другая вымышленная фабула: изменён материал во время ожидания."})
            else:
                holder.execute(text("DELETE FROM reference_evaluation_access_grants "
                                    "WHERE membership_id=:id"), {"id": workspace[3]})
            transaction.commit()
            result = pending.result(timeout=5)
            assert blocked, result.text
            assert result.status_code == (409 if changed == "source" else 403), result.text
        assert _counts(workspace, reference) == (0, 0)


def _lock_reviewer(connection, membership):
    # Match membership administration's User -> ClinicUser order.
    connection.execute(text("SELECT u.id FROM users u JOIN clinic_users c ON c.user_id=u.id "
                            "WHERE c.id=:id FOR UPDATE OF u"), {"id": membership})
    connection.execute(text("SELECT id FROM clinic_users WHERE id=:id FOR UPDATE"),
                       {"id": membership})


def _observed_insert_fk_wait(owner):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with owner.connect() as observer:
            if observer.scalar(text("SELECT count(*) FROM pg_stat_activity "
                "WHERE datname=current_database() AND wait_event_type='Lock' "
                "AND query LIKE 'INSERT INTO %'")):
                return True
        time.sleep(.02)
    return False


@postgres
@pytest.mark.parametrize("withdraw", ["membership", "grant", "user", "subscription",
                                      "subscription-expiry"])
def test_late_approval_fk_wait_cannot_commit_after_reviewer_withdrawal(workspace, monkeypatch,
                                                                   withdraw):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    reviewer = _reviewer(workspace)
    with application_client() as client:
        reference, base, token = _ready(workspace, client)
        key = uuid4()
        if withdraw == "subscription-expiry":
            with workspace[5].begin() as connection:
                connection.execute(text("UPDATE subscription_entitlements "
                    "SET ends_at=clock_timestamp()+interval '2 seconds' "
                    "WHERE user_id=(SELECT user_id FROM clinic_users WHERE id=:id)"),
                    {"id": workspace[3]})
        with workspace[5].connect() as holder, ThreadPoolExecutor(max_workers=1) as pool:
            transaction = holder.begin()
            _lock_reviewer(holder, workspace[3])
            pending = pool.submit(client.post, base + "/review",
                headers=actor_headers(reviewer, key), json={
                    "decision": "APPROVE_FOR_EVALUATION", "expectedReviewSnapshotSha256": token,
                })
            blocked = _observed_insert_fk_wait(workspace[5])
            if withdraw == "membership":
                holder.execute(text("UPDATE clinic_users SET status='REVOKED' WHERE id=:id"),
                               {"id": workspace[3]})
            elif withdraw == "grant":
                holder.execute(text("DELETE FROM reference_evaluation_access_grants "
                    "WHERE membership_id=:id AND permission='REVIEWER'"), {"id": workspace[3]})
            elif withdraw == "user":
                holder.execute(text("UPDATE users SET status='REVOKED' WHERE telegram_user_id=:id"),
                               {"id": reviewer})
            elif withdraw == "subscription":
                holder.execute(text("UPDATE subscription_entitlements SET status='SUSPENDED' "
                    "WHERE user_id=(SELECT user_id FROM clinic_users WHERE id=:id)"),
                    {"id": workspace[3]})
            else:
                time.sleep(2.1)
            transaction.commit()
            result = pending.result(timeout=5)
            assert blocked, result.text
            assert result.status_code == 403, result.text
        assert _counts(workspace, reference) == (0, 0)
        with workspace[5].connect() as connection:
            assert connection.scalar(text("SELECT status FROM reference_evaluation_cases "
                                          "WHERE id=:id"), {"id": reference}) == "READY_FOR_REVIEW"
            assert connection.scalar(text("SELECT count(*) FROM idempotency_records "
                                          "WHERE key=:key"), {"key": str(key)}) == 0


@postgres
@pytest.mark.parametrize("decision", ["APPROVE_FOR_EVALUATION", "CHANGES_REQUIRED"])
def test_late_review_fk_wait_cannot_clear_or_extend_an_expired_original_deadline(workspace,
                                                                              monkeypatch,
                                                                              decision):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    reviewer = _reviewer(workspace)
    with application_client() as client:
        reference, base, token = _ready(workspace, client)
        with workspace[5].begin() as connection:
            connection.execute(text("UPDATE reference_evaluation_case_versions SET "
                "content_expires_at=clock_timestamp()+interval '2 seconds' "
                "WHERE reference_case_id=:id"), {"id": reference})
        key = uuid4()
        with workspace[5].connect() as holder, ThreadPoolExecutor(max_workers=1) as pool:
            transaction = holder.begin()
            _lock_reviewer(holder, workspace[3])
            pending = pool.submit(client.post, base + "/review",
                headers=actor_headers(reviewer, key), json={"decision": decision,
                    "note": "Проверить синтетическую фабулу.",
                    "expectedReviewSnapshotSha256": token})
            blocked = _observed_insert_fk_wait(workspace[5])
            time.sleep(2.1)
            transaction.commit()
            result = pending.result(timeout=5)
            assert blocked, result.text
            assert result.status_code == 410, result.text
        assert _counts(workspace, reference) == (0, 0)
        with workspace[5].connect() as connection:
            assert connection.scalar(text("SELECT status FROM reference_evaluation_cases "
                                          "WHERE id=:id"), {"id": reference}) == "READY_FOR_REVIEW"
            assert connection.scalar(text("SELECT content_expires_at < clock_timestamp() "
                "FROM reference_evaluation_case_versions WHERE reference_case_id=:id"),
                {"id": reference}) is True
            assert connection.scalar(text("SELECT count(*) FROM idempotency_records "
                                          "WHERE key=:key"), {"key": str(key)}) == 0
