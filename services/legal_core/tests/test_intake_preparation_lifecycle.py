"""Real PostgreSQL durable operations, no real people, documents or model calls."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.case_api import ApiError
from legal_core.database import database_url, owner_database_url
from legal_core.intake_preparation import (
    complete_preparation,
    fail_preparation,
    read_preparation,
    reserve_preparation,
)
from legal_core.intake_preparation_contracts import DraftSuggestions
from legal_core.models import IdempotencyRecord
from test_case_api import actor_headers, application_client, seed_admin

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
SOURCE = "Клиника пригласила на осмотр. Пациент сообщает о сколе коронки."


@pytest.fixture
def draft_owner():
    actor = 17_800_000_000 + uuid4().int % 100_000_000
    clinic_id, membership_id = seed_admin(actor)
    with application_client() as client:
        draft = client.post("/v1/telegram-intake-drafts", headers=actor_headers(actor, uuid4()),
                            json={"intakeSchemaVersion": "dental-case-intake.v2"}).json()
        saved = client.put(f"/v1/telegram-intake-drafts/{draft['id']}",
                           headers=actor_headers(actor, uuid4()), json={
                               "expectedRevision": draft["revision"], "wizardState": "EVENT",
                               "draftData": {"intakeVersion": 2, "eventSummary": SOURCE},
                           })
        assert saved.status_code == 200, saved.text
        draft = saved.json()
    return actor, clinic_id, membership_id, draft


def _run(operation):
    async def invoke():
        engine = create_async_engine(database_url())
        try:
            return await operation(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()
    return asyncio.run(invoke())


def _reserve(sessions, actor, draft):
    return reserve_preparation(sessions, telegram_user_id=actor, draft_id=draft["id"],
                               expected_revision=draft["revision"],
                               operation_kind="UNDERSTAND_INTAKE")


def _suggestions():
    return DraftSuggestions.model_validate({
        "clinicActions": ["INVITED_FOR_EXAMINATION"],
        "sourceSpans": {"clinicActions": [{"start": 0, "end": 26}]},
    })


def test_atomic_reservation_commits_before_return_and_restart_does_not_claim_again(draft_owner):
    actor, _, _, draft = draft_owner
    first = _run(lambda sessions: _reserve(sessions, actor, draft))
    second = _run(lambda sessions: _reserve(sessions, actor, draft))
    assert first.state == "PENDING" and first.lease_token is not None
    assert second.operation_id == first.operation_id
    assert second.state == "PENDING" and second.lease_token is None
    assert second.suggestions is None
    # The returned claim cannot rely on a connection/lock left open across a model call.
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '250ms'"))
            assert connection.scalar(text(
                "SELECT id FROM idempotency_records WHERE id=:id FOR UPDATE NOWAIT"
            ), {"id": first.operation_id}) == first.operation_id
    finally:
        engine.dispose()


def test_completed_suggestions_survive_restart_without_changing_confirmed_draft(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    result = _run(lambda sessions: complete_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
        lease_token=lease.lease_token, suggestions=_suggestions(),
    ))
    resumed = _run(lambda sessions: read_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
    ))
    assert result.state == resumed.state == "PROPOSED"
    assert resumed.suggestions == _suggestions()
    assert resumed.lease_token is None
    with application_client() as client:
        actual = client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                            headers=actor_headers(actor)).json()
        assert actual["draftData"] == draft["draftData"]
        assert actual["revision"] == draft["revision"]


def test_wrong_token_cannot_complete_or_destroy_a_live_operation(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    with pytest.raises(ApiError) as error:
        _run(lambda sessions: complete_preparation(
            sessions, telegram_user_id=actor, operation_id=lease.operation_id,
            lease_token=uuid4(), suggestions=_suggestions(),
        ))
    assert error.value.status_code == 409
    assert _run(lambda sessions: read_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
    )).state == "PENDING"


@pytest.mark.parametrize("change", ["revision", "content", "archived", "submitted", "expired"])
def test_stale_completion_never_publishes_after_owner_changes(draft_owner, change):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    assignments = {
        "revision": "revision=revision+1", "content": "draft_json='{}'::jsonb",
        "archived": "status='ARCHIVED'", "submitted": "status='SUBMITTED'",
        "expired": "purge_after=now()-interval '1 minute'",
    }
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text(
                f"UPDATE telegram_intake_drafts SET {assignments[change]} WHERE id=:id"
            ), {"id": draft["id"]})
        with pytest.raises(ApiError) as error:
            _run(lambda sessions: complete_preparation(
                sessions, telegram_user_id=actor, operation_id=lease.operation_id,
                lease_token=lease.lease_token, suggestions=_suggestions(),
            ))
        assert error.value.status_code in {404, 409, 410}
        with engine.connect() as connection:
            row = connection.execute(select(IdempotencyRecord.response_json).where(
                IdempotencyRecord.id == lease.operation_id,
            )).scalar_one()
            assert row is None or row.get("suggestions") is None
    finally:
        engine.dispose()


@pytest.mark.parametrize("access", ["foreign", "other_creator", "subscription", "revoked"])
def test_every_completion_and_read_reauthorizes_without_leaking_proposals(draft_owner, access):
    actor, clinic, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    caller = actor
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            if access == "subscription":
                connection.execute(text(
                    "UPDATE subscription_entitlements SET status='SUSPENDED' WHERE clinic_id=:id"
                ), {"id": clinic})
            elif access == "revoked":
                connection.execute(text(
                    "UPDATE clinic_users SET status='REVOKED' WHERE clinic_id=:id"
                ), {"id": clinic})
            else:
                caller = 18_800_000_000 + uuid4().int % 100_000_000
                other_clinic, other_member = seed_admin(caller)
                if access == "other_creator":
                    # Membership transplant is test setup only; preserve its entitlement FK.
                    connection.execute(text(
                        "DELETE FROM subscription_entitlements WHERE clinic_id=:id"
                    ), {"id": other_clinic})
                    connection.execute(text(
                        "UPDATE clinic_users SET clinic_id=:clinic WHERE id=:id"
                    ), {"clinic": clinic, "id": other_member})
                    connection.execute(text(
                        "INSERT INTO subscription_entitlements "
                        "(clinic_id,user_id,status,plan_code,starts_at) "
                        "SELECT clinic_id,user_id,'ACTIVE','MVP',now()-interval '1 day' "
                        "FROM clinic_users WHERE id=:id"
                    ), {"id": other_member})
        for operation in (read_preparation, complete_preparation):
            extra = {} if operation is read_preparation else {
                "lease_token": lease.lease_token, "suggestions": _suggestions(),
            }
            with pytest.raises(ApiError) as error:
                _run(lambda sessions, operation=operation, extra=extra: operation(
                    sessions, telegram_user_id=caller, operation_id=lease.operation_id, **extra,
                ))
            assert error.value.status_code in {403, 404}
    finally:
        engine.dispose()


def test_unknown_expired_call_falls_back_and_is_never_automatically_reclaimed(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE idempotency_records SET lease_until=:at WHERE id=:id"),
                               {"at": datetime.now(UTC)-timedelta(seconds=1),
                                "id": lease.operation_id})
        resumed = _run(lambda sessions: read_preparation(
            sessions, telegram_user_id=actor, operation_id=lease.operation_id,
        ))
        duplicate = _run(lambda sessions: _reserve(sessions, actor, draft))
        assert resumed.state == duplicate.state == "MANUAL_CONTINUATION"
        assert duplicate.operation_id == lease.operation_id
        assert duplicate.lease_token is None
        with pytest.raises(ApiError):
            _run(lambda sessions: complete_preparation(
                sessions, telegram_user_id=actor, operation_id=lease.operation_id,
                lease_token=lease.lease_token, suggestions=_suggestions(),
            ))
    finally:
        engine.dispose()


def test_concurrent_retries_have_one_durable_operation_and_one_model_claim(draft_owner):
    actor, _, _, draft = draft_owner

    async def concurrent(sessions):
        return await asyncio.gather(*(_reserve(sessions, actor, draft) for _ in range(6)))

    results = _run(concurrent)
    assert len({result.operation_id for result in results}) == 1
    assert sum(result.lease_token is not None for result in results) == 1


def test_model_failure_enables_manual_continuation_without_an_automatic_model_retry(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    failed = _run(lambda sessions: fail_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
        lease_token=lease.lease_token, reason_code="MODEL_TIMEOUT",
    ))
    replay = _run(lambda sessions: _reserve(sessions, actor, draft))
    assert failed.state == replay.state == "MANUAL_CONTINUATION"
    assert failed.suggestions is None and replay.lease_token is None


def test_completed_suggestions_are_not_replayed_after_draft_expiry(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    _run(lambda sessions: complete_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
        lease_token=lease.lease_token, suggestions=_suggestions(),
    ))
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE telegram_intake_drafts SET purge_after=now()-interval '1 minute' "
                "WHERE id=:id"
            ), {"id": draft["id"]})
        with pytest.raises(ApiError) as error:
            _run(lambda sessions: read_preparation(
                sessions, telegram_user_id=actor, operation_id=lease.operation_id,
            ))
        assert error.value.status_code == 410
    finally:
        engine.dispose()


def test_constructed_mutated_suggestion_cannot_bypass_storage_boundary(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    invalid = _suggestions()
    invalid.source_spans["clinicActions"][0].start = True
    with pytest.raises(ValueError):
        _run(lambda sessions: complete_preparation(
            sessions, telegram_user_id=actor, operation_id=lease.operation_id,
            lease_token=lease.lease_token, suggestions=invalid,
        ))
    assert _run(lambda sessions: read_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
    )).state == "PENDING"
