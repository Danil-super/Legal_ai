"""Real PostgreSQL durable operations, no real people, documents or model calls."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.case_api import ApiError
import legal_core.intake_preparation as preparation
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
    assert resumed.source == result.source
    assert resumed.source.draft_id == draft["id"] or str(resumed.source.draft_id) == draft["id"]
    assert resumed.source.draft_revision == draft["revision"]
    assert len(resumed.source.proposal_sha256) == 64
    with application_client() as client:
        actual = client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                            headers=actor_headers(actor)).json()
        assert actual["draftData"] == draft["draftData"]
        assert actual["revision"] == draft["revision"]


def test_shape_valid_cached_proposal_substitution_is_not_confirmable(draft_owner):
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
                "UPDATE idempotency_records SET response_json=jsonb_set(response_json, "
                "'{suggestions,clinicActions}', '[\"NOTHING_YET\"]'::jsonb) WHERE id=:id"
            ), {"id": lease.operation_id})
        with pytest.raises(ApiError) as error:
            _run(lambda sessions: read_preparation(
                sessions, telegram_user_id=actor, operation_id=lease.operation_id,
            ))
        assert error.value.code == "PREPARATION_PROPOSAL_INVALID"
    finally:
        engine.dispose()


@pytest.mark.parametrize("field", ["eventDate", "analysisClarificationAnswers"])
def test_unknown_date_null_survives_completion_restart_and_reservation_replay(draft_owner, field):
    actor, _, _, draft = draft_owner
    unknown = {"date": None, "precision": "UNKNOWN"}
    value = unknown if field == "eventDate" else {"answers": [{
        "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0, "value": unknown,
    }]}
    proposed = DraftSuggestions.model_validate({
        field: value, "sourceSpans": {field: [{"start": 0, "end": len(SOURCE)}]},
    })
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    completed = _run(lambda sessions: complete_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
        lease_token=lease.lease_token, suggestions=proposed,
    ))
    resumed = _run(lambda sessions: read_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
    ))
    replayed = _run(lambda sessions: _reserve(sessions, actor, draft))
    assert completed.state == resumed.state == replayed.state == "PROPOSED"
    assert completed.suggestions == resumed.suggestions == replayed.suggestions == proposed
    assert replayed.lease_token is None
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            stored = connection.scalar(select(IdempotencyRecord.response_json).where(
                IdempotencyRecord.id == lease.operation_id,
            ))["suggestions"]
            assert stored[field] == value
            assert set(stored) == {field, "sourceSpans"}
    finally:
        engine.dispose()
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


@pytest.mark.parametrize("tamper", ["intent", "owner", "resource_type", "span"])
def test_reservation_replay_revalidates_the_persisted_binding_and_grounding(draft_owner, tamper):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    _run(lambda sessions: complete_preparation(
        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
        lease_token=lease.lease_token, suggestions=_suggestions(),
    ))
    assignments = {
        "intent": "response_json=jsonb_set(response_json,'{intent,draftRevision}','99'::jsonb)",
        "owner": "resource_id=gen_random_uuid()",
        "resource_type": "resource_type='TELEGRAM_INTAKE_DRAFT'",
        "span": "response_json=jsonb_set(response_json,"
                "'{suggestions,sourceSpans,clinicActions,0,end}','1499'::jsonb)",
    }
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text(
                f"UPDATE idempotency_records SET {assignments[tamper]} WHERE id=:id"
            ), {"id": lease.operation_id})
        with pytest.raises((ApiError, ValueError)):
            _run(lambda sessions: _reserve(sessions, actor, draft))
    finally:
        engine.dispose()


def test_retention_and_expiring_completion_share_lock_order_and_never_publish_expired_data(
    draft_owner, monkeypatch,
):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    owner = create_engine(owner_database_url())
    label = "preparation-retention-test-" + uuid4().hex
    try:
        with owner.begin() as connection:
            expires_at = connection.scalar(text(
                "UPDATE telegram_intake_drafts "
                "SET purge_after=clock_timestamp()+interval '2 seconds' "
                "WHERE id=:id RETURNING purge_after"
            ), {"id": draft["id"]})

        async def scenario(sessions):
            locked, resume = asyncio.Event(), asyncio.Event()
            original = preparation._draft

            async def delayed(*args, **kwargs):
                result = await original(*args, **kwargs)
                locked.set()
                await resume.wait()
                return result

            monkeypatch.setattr(preparation, "_draft", delayed)

            async def purge():
                async with sessions() as session:
                    await session.execute(
                        text("SELECT set_config('application_name', :label, true)"),
                        {"label": label},
                    )
                    await session.execute(text(
                        "SELECT public.purge_expired_telegram_intake_drafts()",
                    ))
                    await session.commit()

            completion = asyncio.create_task(complete_preparation(
                sessions, telegram_user_id=actor, operation_id=lease.operation_id,
                lease_token=lease.lease_token, suggestions=_suggestions(),
            ))
            await asyncio.wait_for(locked.wait(), timeout=3)
            await asyncio.sleep(max(0, (expires_at-datetime.now(UTC)).total_seconds()) + 0.03)
            retention = asyncio.create_task(purge())
            try:
                for _ in range(100):
                    with owner.connect() as connection:
                        waiting = connection.scalar(text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE "
                            "application_name=:label AND wait_event_type='Lock')"
                        ), {"label": label})
                    if waiting:
                        break
                    await asyncio.sleep(0.02)
                else:
                    pytest.fail("retention did not reach its conflicting draft lock")
            finally:
                resume.set()
            results = await asyncio.wait_for(
                asyncio.gather(completion, retention, return_exceptions=True), timeout=8,
            )
            assert isinstance(results[0], ApiError) and results[0].status_code == 410
            assert results[1] is None, f"retention must not deadlock: {type(results[1]).__name__}"

        _run(scenario)
        with owner.connect() as connection:
            response = connection.scalar(text(
                "SELECT response_json FROM idempotency_records WHERE id=:id"
            ), {"id": lease.operation_id})
            assert response is None
    finally:
        owner.dispose()


@pytest.mark.parametrize("withdraw", ["membership", "subscription", "natural_expiry"])
def test_access_is_rechecked_after_waiting_for_the_draft_lock(draft_owner, monkeypatch, withdraw):
    actor, clinic, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    original = preparation._draft
    engine = create_engine(owner_database_url())

    async def withdraw_after_gate(*args, **kwargs):
        result = await original(*args, **kwargs)
        statement = {
            "membership": "UPDATE clinic_users SET status='REVOKED' WHERE clinic_id=:id",
            "subscription": (
                "UPDATE subscription_entitlements SET status='SUSPENDED' WHERE clinic_id=:id"
            ),
            "natural_expiry": (
                "UPDATE subscription_entitlements "
                "SET ends_at=clock_timestamp()+interval '0.1 seconds' WHERE clinic_id=:id"
            ),
        }[withdraw]
        with engine.begin() as connection:
            connection.execute(text(statement), {"id": clinic})
        if withdraw == "natural_expiry":
            await asyncio.sleep(0.15)
        return result

    monkeypatch.setattr(preparation, "_draft", withdraw_after_gate)
    try:
        with pytest.raises(ApiError) as error:
            _run(lambda sessions: complete_preparation(
                sessions, telegram_user_id=actor, operation_id=lease.operation_id,
                lease_token=lease.lease_token, suggestions=_suggestions(),
            ))
        assert error.value.status_code == 403
    finally:
        engine.dispose()
