"""Independent synthetic PostgreSQL probes; no providers, patient files or production."""

import asyncio
import os
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.case_api import ApiError, resolve_actor
from legal_core.database import database_url, owner_database_url
from legal_core.draft_retention import purge_expired_intake_drafts
from legal_core.intake_answer_operations import cancel_answer, read_answer, reserve_answer
from legal_core.intake_answer_acceptance import accept_answer
from legal_core.intake_preparation import (
    cancel_preparation,
    read_preparation,
    reserve_preparation,
)
from test_case_api import seed_admin
from test_intake_preparation_lifecycle import _run, draft_owner
from test_intake_answer_consent import proposed, consent

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]
ANSWER = "Synthetic clinic narrative for a factual question."


def reserve(sessions, actor, draft, answer=ANSWER):
    return reserve_answer(sessions, telegram_user_id=actor, draft_id=draft["id"],
                          expected_revision=draft["revision"], answer=answer)


def test_independent_different_answer_race_has_one_winner_and_no_second_lease(draft_owner):
    actor, _, _, draft = draft_owner

    async def race(sessions):
        return await asyncio.gather(
            reserve(sessions, actor, draft),
            reserve(sessions, actor, draft, "A different synthetic account."),
            return_exceptions=True,
        )

    results = _run(race)
    winners = [item for item in results if not isinstance(item, Exception)]
    refusals = [item for item in results if isinstance(item, ApiError)]
    assert len(winners) == len(refusals) == 1
    assert winners[0].lease_token is not None
    assert refusals[0].code == "ANSWER_OPERATION_PENDING"


def test_independent_old_and_new_decoders_do_not_mutate_each_others_operations(draft_owner):
    actor, _, _, draft = draft_owner
    answer = _run(lambda sessions: reserve(sessions, actor, draft))
    narrative = _run(lambda sessions: reserve_preparation(
        sessions, telegram_user_id=actor, draft_id=draft["id"],
        expected_revision=draft["revision"], operation_kind="UNDERSTAND_INTAKE",
    ))
    for action, operation_id in (
        (read_answer, narrative.operation_id), (cancel_answer, narrative.operation_id),
        (read_preparation, answer.response.operation_id),
        (cancel_preparation, answer.response.operation_id),
    ):
        with pytest.raises(ApiError) as caught:
            _run(lambda sessions, action=action, operation_id=operation_id: action(
                sessions, telegram_user_id=actor, operation_id=operation_id,
            ))
        assert caught.value.status_code == 409
        assert caught.value.__context__ is None
    assert _run(lambda sessions: read_answer(
        sessions, telegram_user_id=actor, operation_id=answer.response.operation_id,
    )).response.state == "PENDING"
    assert _run(lambda sessions: read_preparation(
        sessions, telegram_user_id=actor, operation_id=narrative.operation_id,
    )).state == "PENDING"


@pytest.mark.parametrize("change", ["revision", "content", "wizard", "archived", "expired"])
def test_independent_saved_draft_changes_fence_old_answer_reads_and_cancellation(
    draft_owner, change,
):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: reserve(sessions, actor, draft))
    assignments = {
        "revision": "revision=revision+1", "content": "draft_json='{}'::jsonb",
        "wizard": "wizard_state='HEALTH'", "archived": "status='ARCHIVED'",
        "expired": "purge_after=now()-interval '1 minute'",
    }
    owner = create_engine(owner_database_url())
    try:
        with owner.begin() as connection:
            connection.execute(text(
                f"UPDATE telegram_intake_drafts SET {assignments[change]} WHERE id=:id"
            ), {"id": draft["id"]})
        for action in (read_answer, cancel_answer):
            with pytest.raises(ApiError) as caught:
                _run(lambda sessions, action=action: action(
                    sessions, telegram_user_id=actor, operation_id=lease.response.operation_id,
                ))
            assert caught.value.status_code in {404, 409, 410}
    finally:
        owner.dispose()


def test_independent_same_clinic_other_creator_cannot_read_or_cancel_raw_answer(draft_owner):
    actor, clinic, _, draft = draft_owner
    lease = _run(lambda sessions: reserve(sessions, actor, draft))
    other = 98_100_000_000 + uuid4().int % 100_000_000
    _, member = seed_admin(other, entitlement_status=None)
    owner = create_engine(owner_database_url())
    try:
        with owner.begin() as connection:
            user_id = connection.scalar(text("UPDATE clinic_users SET clinic_id=:clinic "
                                             "WHERE id=:member RETURNING user_id"),
                                        {"clinic": clinic, "member": member})
            connection.execute(text("INSERT INTO subscription_entitlements "
                                    "(clinic_id,user_id,status,plan_code,starts_at) "
                                    "VALUES (:clinic,:user,'ACTIVE','MVP',now()-interval '1 day')"),
                               {"clinic": clinic, "user": user_id})
        for action in (read_answer, cancel_answer):
            with pytest.raises(ApiError) as caught:
                _run(lambda sessions, action=action: action(
                    sessions, telegram_user_id=other, operation_id=lease.response.operation_id,
                ))
            assert caught.value.status_code == 404
    finally:
        owner.dispose()


def test_independent_runtime_rls_hides_foreign_clinic_answer_even_without_application_filter(
    draft_owner,
):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: reserve(sessions, actor, draft))
    other = 98_200_000_000 + uuid4().int % 100_000_000
    seed_admin(other)

    async def direct_read(sessions):
        async with sessions() as session:
            await resolve_actor(session, other)
            return await session.scalar(text("SELECT response_json FROM idempotency_records "
                                             "WHERE id=:id"),
                                        {"id": lease.response.operation_id})

    assert _run(direct_read) is None


def test_independent_retention_scrubs_answer_text_even_with_new_scope(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: reserve(sessions, actor, draft))
    owner = create_engine(owner_database_url())
    try:
        with owner.begin() as connection:
            connection.execute(text("UPDATE telegram_intake_drafts "
                                    "SET purge_after=now()-interval '1 minute' WHERE id=:id"),
                               {"id": draft["id"]})
        assert _run(purge_expired_intake_drafts) >= 1
        with owner.connect() as connection:
            row = connection.execute(text("SELECT response_json,lease_token,lease_until,state "
                                          "FROM idempotency_records WHERE id=:id"),
                                     {"id": lease.response.operation_id}).one()
            assert tuple(row) == (None, None, None, "FAILED")
    finally:
        owner.dispose()


@pytest.mark.parametrize("change", ["role", "revoke", "entitlement", "expiry"])
def test_independent_reservation_rechecks_authority_after_actual_draft_lock_wait(
    draft_owner, change,
):
    actor, clinic, member, draft = draft_owner
    owner = create_engine(owner_database_url())
    name = f"answer-review-{uuid4()}"

    async def probe():
        engine = create_async_engine(database_url(), connect_args={"application_name": name})
        try:
            with owner.connect() as locker:
                transaction = locker.begin()
                locker.execute(text("SELECT id FROM telegram_intake_drafts "
                                    "WHERE id=:id FOR UPDATE"),
                               {"id": draft["id"]})
                task = asyncio.create_task(reserve(async_sessionmaker(
                    engine, expire_on_commit=False,
                ), actor, draft))
                try:
                    deadline = time.monotonic() + 5
                    while True:
                        with owner.connect() as monitor:
                            waiting = monitor.scalar(text("SELECT xact_start FROM pg_stat_activity "
                                                          "WHERE application_name=:name "
                                                          "AND wait_event_type='Lock'"),
                                                     {"name": name})
                        if waiting:
                            break
                        if task.done() or time.monotonic() > deadline:
                            pytest.fail("reservation did not actually wait on the locked draft")
                        await asyncio.sleep(0.02)
                    with owner.begin() as changer:
                        if change in {"role", "revoke"}:
                            assignment = ("role='CLINIC_OWNER'" if change == "role" else
                                          "status='REVOKED'")
                            changer.execute(text("UPDATE clinic_users SET "
                                                 f"{assignment} WHERE id=:id"),
                                            {"id": member})
                        else:
                            if change == "expiry":
                                await asyncio.sleep(0.05)
                            assignment = ("status='SUSPENDED'" if change == "entitlement" else
                                          "ends_at=clock_timestamp()-interval '1 millisecond'")
                            changer.execute(text("UPDATE subscription_entitlements SET "
                                                 f"{assignment} WHERE clinic_id=:id"),
                                            {"id": clinic})
                            if change == "expiry":
                                ends_at = changer.scalar(text("SELECT ends_at FROM "
                                                              "subscription_entitlements "
                                                              "WHERE clinic_id=:id"),
                                                         {"id": clinic})
                                # Transaction-start now() would still see access.
                                assert ends_at > waiting
                    transaction.commit()
                    with pytest.raises(ApiError) as caught:
                        await asyncio.wait_for(task, timeout=5)
                    assert caught.value.status_code == 403
                finally:
                    if transaction.is_active:
                        transaction.rollback()
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
        finally:
            await engine.dispose()

    try:
        asyncio.run(probe())
        with owner.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM idempotency_records "
                                          "WHERE resource_id=:id "
                                          "AND resource_type='INTAKE_PREPARATION'"),
                                     {"id": draft["id"]}) == 0
    finally:
        owner.dispose()


def test_independent_accepted_current_revision_rejects_unversioned_content_substitution(
    draft_owner,
):
    status = proposed(draft_owner)
    actor = draft_owner[0]
    request = consent(status)
    _run(lambda sessions: accept_answer(sessions, telegram_user_id=actor,
                                        operation_id=status.operation_id, request=request))
    owner = create_engine(owner_database_url())
    try:
        with owner.begin() as connection:
            connection.execute(text("UPDATE telegram_intake_drafts "
                                    "SET draft_json=jsonb_set(draft_json,'{eventSummary}',"
                                    "'\"A substituted synthetic narrative.\"'::jsonb) "
                                    "WHERE id=:id"),
                               {"id": draft_owner[3]["id"]})
        for action in (read_answer, accept_answer):
            with pytest.raises(ApiError) as caught:
                _run(lambda sessions, action=action: action(
                    sessions, telegram_user_id=actor, operation_id=status.operation_id,
                    **({"request": request} if action is accept_answer else {}),
                ))
            assert caught.value.code == "ANSWER_ACCEPTANCE_INVALID"
    finally:
        owner.dispose()


def test_independent_concurrent_cancel_and_acceptance_cannot_undo_confirmed_draft(draft_owner):
    status = proposed(draft_owner)
    actor, _, _, draft = draft_owner

    async def race(sessions):
        return await asyncio.gather(
            accept_answer(sessions, telegram_user_id=actor,
                          operation_id=status.operation_id, request=consent(status)),
            cancel_answer(sessions, telegram_user_id=actor, operation_id=status.operation_id),
            return_exceptions=True,
        )

    accepted, cancelled = _run(race)
    current = _run(lambda sessions: read_answer(
        sessions, telegram_user_id=actor, operation_id=status.operation_id,
    )).response
    if isinstance(accepted, ApiError):
        assert not isinstance(cancelled, Exception)
        assert cancelled.response.state == current.state == "MANUAL_CONTINUATION"
    else:
        assert isinstance(cancelled, ApiError) and cancelled.code == "ANSWER_ALREADY_ACCEPTED"
        assert current.state == "ACCEPTED"
        assert accepted.draft.revision == draft["revision"] + 1


@pytest.mark.parametrize("action", ["read", "accept", "cancel"])
def test_independent_all_completed_operations_reauthorize_current_entitlement(draft_owner, action):
    status = proposed(draft_owner)
    actor, clinic, _, _ = draft_owner
    owner = create_engine(owner_database_url())
    try:
        with owner.begin() as connection:
            connection.execute(text("UPDATE subscription_entitlements SET status='SUSPENDED' "
                                    "WHERE clinic_id=:id"), {"id": clinic})
        selected = {"read": read_answer, "accept": accept_answer, "cancel": cancel_answer}[action]
        with pytest.raises(ApiError) as caught:
            _run(lambda sessions: selected(
                sessions, telegram_user_id=actor, operation_id=status.operation_id,
                **({"request": consent(status)} if action == "accept" else {}),
            ))
        assert caught.value.status_code == 403
    finally:
        owner.dispose()
