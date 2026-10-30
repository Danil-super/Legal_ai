"""Adversarial report-free origin/access checks, using fictional cases only."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_case_api import actor_headers, seed_admin
from test_preparation_analysis import _submission
from test_preparation_analysis import prepared_case as prepared_case
from test_preparation_recovery import _binding, _late, _preview, _recover
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("tamper", ["operation", "digest", "facts", "receipt", "latest"])
def test_stale_or_tampered_origin_does_not_create_or_replay_a_draft(prepared_case, tamper):
    client, actor, _, case, headers, context, engine = prepared_case
    result = _late(prepared_case)
    key = uuid4()
    assert _recover(client, actor, case, result, key).status_code == 201
    binding = _binding(result)
    if tamper == "operation":
        binding["expectedOperationId"] = str(uuid4())
    elif tamper == "digest":
        binding["expectedOperationSha256"] = "0" * 64
    elif tamper == "facts":
        binding["expectedFactSnapshotSha256"] = "0" * 64
    elif tamper == "receipt":
        wire = deepcopy(result)
        wire["source"]["factSnapshotSha256"] = "0" * 64
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE idempotency_records SET response_json=CAST(:wire AS jsonb) "
                "WHERE scope=:scope AND key=:key"
            ), {"wire": json.dumps(wire), "scope": f"cases:{case}:preparation-submissions",
                "key": headers["Idempotency-Key"]})
    else:
        newer = client.post(f"/v1/cases/{case}/preparation-submissions",
                            headers=headers | {"Idempotency-Key": str(uuid4())},
                            json=_submission(context, questions=False, claims=True))
        assert newer.status_code == 201 and newer.json()["status"] == "READY", newer.text
    rejected = client.post(f"/v1/cases/{case}/clarification-drafts", json=binding,
                           headers=actor_headers(actor, key))
    assert rejected.status_code == (502 if tamper == "receipt" else 409), rejected.text
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM telegram_intake_drafts WHERE clinic_id="
            "(SELECT clinic_id FROM cases WHERE id=:case)"
        ), {"case": case}) == 1


@pytest.mark.parametrize("outsider_kind", ["foreign", "same_clinic", "lawyer"])
def test_preview_and_recovery_remain_creator_only(prepared_case, outsider_kind):
    client, actor, clinic, case, _, _, engine = prepared_case
    result = _late(prepared_case)
    outsider = actor + 1_000_000_000
    if outsider_kind == "foreign":
        seed_admin(outsider)
    else:
        user, member = uuid4(), uuid4()
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users(id,telegram_user_id) VALUES(:id,:actor)"),
                               {"id": user, "actor": outsider})
            connection.execute(text(
                "INSERT INTO clinic_users(id,clinic_id,user_id,role) "
                "VALUES(:id,:clinic,:user,:role)"
            ), {"id": member, "clinic": clinic, "user": user,
                "role": "CLINIC_LAWYER" if outsider_kind == "lawyer" else "CLINIC_ADMIN"})
            connection.execute(text(
                "INSERT INTO subscription_entitlements"
                "(clinic_id,user_id,status,plan_code,starts_at) "
                "VALUES(:clinic,:user,'ACTIVE','MVP',clock_timestamp())"
            ), {"clinic": clinic, "user": user})
    # Lawyers may create their own cases; every non-creator still sees no foreign case.
    preview = _preview(client, outsider, case, result["source"]["operationId"])
    assert preview.status_code == 404, preview.text
    assert preview.json()["error"]["code"] == "CASE_NOT_FOUND"
    rejected = _recover(client, outsider, case, result)
    assert rejected.status_code == 404, rejected.text
    assert rejected.json()["error"]["code"] == "CASE_NOT_FOUND"


@pytest.mark.parametrize("expiry", ["case", "draft", "membership"])
def test_replay_checks_current_access_and_retention(prepared_case, expiry):
    client, actor, clinic, case, _, _, engine = prepared_case
    result = _late(prepared_case)
    key = uuid4()
    draft = _recover(client, actor, case, result, key).json()
    with engine.begin() as connection:
        if expiry == "case":
            connection.execute(text("UPDATE cases SET retention_due_at=clock_timestamp()-"
                                    "interval '1 second' WHERE id=:id"), {"id": case})
        elif expiry == "draft":
            connection.execute(text("UPDATE telegram_intake_drafts SET purge_after="
                                    "clock_timestamp()-interval '1 second' WHERE id=:id"),
                               {"id": draft["id"]})
        else:
            connection.execute(text("UPDATE clinic_users SET status='REVOKED' "
                                    "WHERE clinic_id=:clinic"), {"clinic": clinic})
    rejected = _recover(client, actor, case, result, key)
    assert rejected.status_code == (403 if expiry == "membership" else 410), rejected.text


@pytest.mark.parametrize("tamper", ["bad_id", "foreign_origin", "invented_summary"])
def test_mutable_draft_cache_cannot_select_another_origin_or_invent_facts(prepared_case, tamper):
    client, actor, _, case, _, _, engine = prepared_case
    result = _late(prepared_case)
    key = uuid4()
    draft = _recover(client, actor, case, result, key).json()
    wire = deepcopy(draft)
    if tamper == "bad_id":
        wire["id"] = "malformed"
    elif tamper == "foreign_origin":
        wire["draftData"]["correctionSource"]["operationId"] = str(uuid4())
    else:
        wire["draftData"]["eventSummary"] = "Вымышленная подмена содержания кеша."
    with engine.begin() as connection:
        connection.execute(text("UPDATE idempotency_records SET response_json=CAST(:wire AS jsonb) "
                                "WHERE scope=:scope AND key=:key"),
                           {"wire": json.dumps(wire), "scope": f"case-clarification:{case}",
                            "key": str(key)})
    replay = _recover(client, actor, case, result, key)
    if tamper == "invented_summary":
        assert replay.status_code == 200 and replay.json() == draft
    else:
        assert replay.status_code == 502, replay.text


def test_concurrent_identical_recovery_creates_one_draft(prepared_case):
    client, actor, _, case, _, _, engine = prepared_case
    result = _late(prepared_case)
    key = uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: _recover(client, actor, case, result, key), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 201]
    assert responses[0].json() == responses[1].json()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM telegram_intake_drafts")) == 1


def test_valid_same_origin_draft_cannot_replace_another_intents_receipt(prepared_case):
    client, actor, _, case, _, _, engine = prepared_case
    result = _late(prepared_case)
    original_key = uuid4()
    original = _recover(client, actor, case, result, original_key).json()
    other = _recover(client, actor, case, result).json()
    assert original["id"] != other["id"]
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE idempotency_records SET resource_id=:other,response_json=CAST(:wire AS jsonb) "
            "WHERE scope=:scope AND key=:key"
        ), {"other": other["id"], "wire": json.dumps(other),
            "scope": f"case-clarification:{case}", "key": str(original_key)})
    replay = _recover(client, actor, case, result, original_key)
    assert replay.status_code == 502, replay.text
    assert replay.json()["error"]["code"] == "CLARIFICATION_RESULT_INVALID"


@pytest.mark.parametrize(("boundary", "withdrawal"), [
    ("case", "membership"), ("case", "case_expiry"), ("member", "natural_expiry"),
])
def test_recovery_rechecks_after_real_database_lock_wait(prepared_case, boundary, withdrawal):
    client, actor, clinic, case, _, _, engine = prepared_case
    result = _late(prepared_case)
    with engine.connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = blocker.begin()
        blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
        if boundary == "case":
            blocker.execute(text("SELECT id FROM cases WHERE id=:case FOR UPDATE"), {"case": case})
        else:
            blocker.execute(text("SELECT id FROM clinic_users WHERE clinic_id=:clinic FOR UPDATE"),
                            {"clinic": clinic})
        future = pool.submit(_recover, client, actor, case, result)
        try:
            deadline = time.monotonic() + 5
            blocked = False
            while time.monotonic() < deadline:
                with engine.connect() as observer:
                    blocked = observer.scalar(text(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                        "WHERE :blocker=ANY(pg_blocking_pids(pid)))"
                    ), {"blocker": blocker_pid})
                if blocked:
                    break
                time.sleep(0.01)
            assert blocked, "request must actually wait on the locked database row"
            statements = {
                "membership": "UPDATE clinic_users SET status='REVOKED' WHERE clinic_id=:clinic",
                "case_expiry": "UPDATE cases SET retention_due_at=clock_timestamp() "
                "-interval '1 second' WHERE id=:case",
                "natural_expiry": "UPDATE subscription_entitlements SET ends_at=clock_timestamp() "
                "-interval '1 microsecond' WHERE clinic_id=:clinic",
            }
            blocker.execute(text(statements[withdrawal]), {"clinic": clinic, "case": case})
            transaction.commit()
        finally:
            if transaction.is_active:
                transaction.rollback()
        rejected = future.result(15)
    assert rejected.status_code == (410 if withdrawal == "case_expiry" else 403), rejected.text
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM telegram_intake_drafts")) == 0
