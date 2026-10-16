"""Independent acceptance lock-wait, receipt and race evidence; synthetic data."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.case_api import ApiError
from legal_core.database import owner_database_url
import legal_core.intake_preparation as preparation
from test_case_api import seed_admin
from test_intake_preparation_acceptance import _accept, _proposal
from test_intake_preparation_lifecycle import _run, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires reviewer disposable PostgreSQL")
__all__ = ["draft_owner"]


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("change", ["member", "user", "subscription", "natural", "expired", "edit"])
def test_current_access_retention_and_source_after_actual_row_lock_wait(
    draft_owner, replay, change,
):
    actor, clinic, member, draft = draft_owner
    result, request = _proposal(actor, draft)
    if replay:
        _run(lambda sessions: _accept(sessions, actor, result, request))
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as holder:
            holder.execute(text("SELECT id FROM telegram_intake_drafts WHERE id=:id FOR UPDATE"),
                           {"id": draft["id"]})
            blocker = holder.scalar(text("SELECT pg_backend_pid()"))

            async def scenario(sessions):
                task = asyncio.create_task(_accept(sessions, actor, result, request))
                try:
                    blocked = False
                    for _ in range(200):
                        holder.execute(text("SELECT pg_stat_clear_snapshot()"))
                        blocked = holder.scalar(text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE :pid = ANY(pg_blocking_pids(pid)))"
                        ), {"pid": blocker})
                        if blocked:
                            break
                        await asyncio.sleep(.01)
                    assert blocked, "acceptance must actually wait on the owned draft lock"
                    statements = {
                        "member": "UPDATE clinic_users SET status='REVOKED' WHERE id=:member",
                        "user": "UPDATE users SET status='BLOCKED' WHERE id IN "
                                "(SELECT user_id FROM clinic_users WHERE id=:member)",
                        "subscription": "UPDATE subscription_entitlements SET status='SUSPENDED' "
                                        "WHERE clinic_id=:clinic",
                        "natural": "UPDATE subscription_entitlements SET "
                                   "ends_at=clock_timestamp()-interval '1 millisecond' "
                                   "WHERE clinic_id=:clinic",
                        "expired": "UPDATE telegram_intake_drafts SET "
                                   "purge_after=clock_timestamp()-interval '1 second' WHERE id=:id",
                        "edit": "UPDATE telegram_intake_drafts SET revision=revision+1, "
                                "draft_json=jsonb_set(draft_json,'{healthSignals}', "
                                "'[\"UNKNOWN\"]'::jsonb) WHERE id=:id",
                    }
                    holder.execute(text(statements[change]),
                                   {"id": draft["id"], "clinic": clinic, "member": member})
                    holder.commit()
                    if change == "edit" and replay:
                        saved = await asyncio.wait_for(task, timeout=3)
                        assert saved.draft.draft_data["healthSignals"] == ["UNKNOWN"]
                        assert saved.accepted_revision == draft["revision"] + 1
                    else:
                        with pytest.raises(ApiError) as caught:
                            await asyncio.wait_for(task, timeout=3)
                        assert caught.value.status_code in {403, 409, 410}
                        assert caught.value.__cause__ is None
                        assert caught.value.__context__ is None
                finally:
                    holder.rollback()
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            _run(scenario)
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT count(*) FROM audit_events WHERE resource_id=:id "
                "AND action='INTAKE_PREPARATION_ACCEPTED'"
            ), {"id": result.operation_id}) == int(replay)
    finally:
        engine.dispose()


@pytest.mark.parametrize("mutation", ["suggestion", "output", "audit_missing", "nonobject"])
def test_replayed_acceptance_rejects_substituted_receipt_and_hides_raw_context(
    draft_owner, mutation,
):
    actor, _, _, draft = draft_owner
    result, request = _proposal(actor, draft)
    _run(lambda sessions: _accept(sessions, actor, result, request))
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            statements = {
                "suggestion": "UPDATE idempotency_records SET response_json=jsonb_set("
                              "response_json,'{suggestions,clinicActions}', "
                              "'[\"NOTHING_YET\"]'::jsonb) WHERE id=:id",
                "output": "UPDATE idempotency_records SET response_json=jsonb_set("
                          "response_json,'{acceptance,outputSha256}',to_jsonb(repeat('0',64))) "
                          "WHERE id=:id",
                "audit_missing": "DELETE FROM audit_events WHERE resource_id=:id "
                                 "AND action='INTAKE_PREPARATION_ACCEPTED'",
                "nonobject": "UPDATE idempotency_records SET response_json=jsonb_set("
                             "response_json,'{acceptance}', "
                             "'[\"synthetic-private-marker\"]'::jsonb) WHERE id=:id",
            }
            connection.execute(text(statements[mutation]), {"id": result.operation_id})
        for callback in (
            lambda sessions: _accept(sessions, actor, result, request),
            lambda sessions: preparation.read_preparation(
                sessions, telegram_user_id=actor, operation_id=result.operation_id,
            ),
        ):
            with pytest.raises(ApiError) as caught:
                _run(callback)
            assert caught.value.code == "PREPARATION_ACCEPTANCE_INVALID"
            assert caught.value.__context__ is None and caught.value.__cause__ is None
            assert "synthetic-private-marker" not in str(caught.value)
    finally:
        engine.dispose()


def test_actual_cancel_accept_race_has_one_terminal_winner(draft_owner):
    actor, _, _, draft = draft_owner
    result, request = _proposal(actor, draft)

    async def race(sessions):
        return await asyncio.gather(
            _accept(sessions, actor, result, request),
            preparation.cancel_preparation(sessions, telegram_user_id=actor,
                                           operation_id=result.operation_id),
            return_exceptions=True,
        )

    results = _run(race)
    errors = [item for item in results if isinstance(item, ApiError)]
    assert len(errors) == 1 and errors[0].status_code == 409
    state = _run(lambda sessions: preparation.read_preparation(
        sessions, telegram_user_id=actor, operation_id=result.operation_id,
    ))
    assert state.state in {"ACCEPTED", "MANUAL_CONTINUATION"}


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("same_clinic", [False, True])
def test_foreign_or_same_clinic_other_creator_cannot_accept_or_read_receipt(
    draft_owner, replay, same_clinic,
):
    actor, clinic, _, draft = draft_owner
    result, request = _proposal(actor, draft)
    if replay:
        _run(lambda sessions: _accept(sessions, actor, result, request))
    caller = 23_800_000_000 + uuid4().int % 100_000_000
    other_clinic, other_member = seed_admin(caller)
    engine = create_engine(owner_database_url())
    try:
        if same_clinic:
            with engine.begin() as connection:
                connection.execute(text(
                    "DELETE FROM subscription_entitlements WHERE clinic_id=:id"
                ), {"id": other_clinic})
                connection.execute(text("UPDATE clinic_users SET clinic_id=:clinic WHERE id=:id"),
                                   {"clinic": clinic, "id": other_member})
                connection.execute(text(
                    "INSERT INTO subscription_entitlements "
                    "(clinic_id,user_id,status,plan_code,starts_at) "
                    "SELECT clinic_id,user_id,'ACTIVE','MVP',now()-interval '1 day' "
                    "FROM clinic_users WHERE id=:id"
                ), {"id": other_member})
        for callback in (
            lambda sessions: _accept(sessions, caller, result, request),
            lambda sessions: preparation.read_preparation(
                sessions, telegram_user_id=caller, operation_id=result.operation_id,
            ),
            lambda sessions: preparation.cancel_preparation(
                sessions, telegram_user_id=caller, operation_id=result.operation_id,
            ),
        ):
            with pytest.raises(ApiError) as caught:
                _run(callback)
            assert caught.value.status_code == 404
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT count(*) FROM audit_events WHERE resource_id=:id "
                "AND action='INTAKE_PREPARATION_ACCEPTED'"
            ), {"id": result.operation_id}) == int(replay)
    finally:
        engine.dispose()
