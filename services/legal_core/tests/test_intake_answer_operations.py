"""Answer reservation is durable, private and distinct from narrative preparation."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.case_api import ApiError
import legal_core.intake_answer_operations as operations_module
from legal_core.database import owner_database_url
from legal_core.intake_answer_operations import (
    cancel_answer,
    discover_answer,
    read_answer,
    reserve_answer,
)
from test_case_api import actor_headers, application_client, seed_admin
from test_intake_preparation_lifecycle import _run, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]
ANSWER = "Произошёл скол установленной коронки, обратились впервые."


@pytest.mark.parametrize("cancel", [False, True])
def test_exact_answer_retry_across_date_boundary_reuses_frozen_intent(
    draft_owner, monkeypatch, cancel,
):
    actor, _, _, draft = draft_owner
    # Keep both simulated instants in the future of the actual DB clock/retention.
    instant = datetime.now(UTC).replace(hour=23, minute=59, second=59)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant

    monkeypatch.setattr(operations_module, "datetime", Clock)
    first = _run(lambda sessions: _reserve(sessions, actor, draft))
    if cancel:
        _run(lambda sessions: cancel_answer(
            sessions, telegram_user_id=actor, operation_id=first.response.operation_id,
        ))
    instant += timedelta(seconds=2)
    replay = _run(lambda sessions: _reserve(sessions, actor, draft))
    assert replay.response.operation_id == first.response.operation_id
    assert replay.lease_token is None and replay.intent is None
    assert replay.response.state == ("MANUAL_CONTINUATION" if cancel else "PENDING")


def _reserve(sessions, actor, draft, answer=ANSWER):
    return reserve_answer(sessions, telegram_user_id=actor, draft_id=draft["id"],
                          expected_revision=draft["revision"], answer=answer)


def test_concurrent_same_answer_retries_reserve_once_and_never_update_confirmed_draft(draft_owner):
    actor, _, _, draft = draft_owner

    async def concurrent(sessions):
        return await asyncio.gather(*[_reserve(sessions, actor, draft) for _ in range(4)])

    operations = _run(concurrent)
    assert len({item.response.operation_id for item in operations}) == 1
    assert sum(item.lease_token is not None for item in operations) == 1
    assert all(item.response.state == "PENDING" for item in operations)
    found = _run(lambda sessions: discover_answer(
        sessions, telegram_user_id=actor, draft_id=draft["id"],
    ))
    assert found.response == operations[0].response
    assert found.lease_token is None and found.intent is None
    with application_client() as client:
        assert client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                          headers=actor_headers(actor)).json() == draft
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            audits = connection.execute(text(
                "SELECT metadata_json FROM audit_events WHERE resource_id=:id"
            ), {"id": found.response.operation_id}).scalars().all()
            assert ANSWER not in str(audits)
    finally:
        engine.dispose()


def test_different_answer_requires_explicit_cancel_and_does_not_select_latest(draft_owner):
    actor, _, _, draft = draft_owner
    first = _run(lambda sessions: _reserve(sessions, actor, draft))
    with pytest.raises(ApiError) as error:
        _run(lambda sessions: _reserve(sessions, actor, draft, "Другое описание обстоятельств."))
    assert error.value.code == "ANSWER_OPERATION_PENDING"
    cancelled = _run(lambda sessions: cancel_answer(
        sessions, telegram_user_id=actor, operation_id=first.response.operation_id,
    ))
    assert cancelled.response.state == "MANUAL_CONTINUATION"
    assert _run(lambda sessions: _reserve(sessions, actor, draft)).response == cancelled.response
    second = _run(lambda sessions: _reserve(
        sessions, actor, draft, "Другое описание обстоятельств.",
    ))
    assert second.lease_token is not None
    assert second.response.operation_id != first.response.operation_id
    found = _run(lambda sessions: discover_answer(
        sessions, telegram_user_id=actor, draft_id=draft["id"],
    ))
    assert found.response.operation_id == second.response.operation_id


@pytest.mark.parametrize("answer", ["", " " * 10, "x" * 1501, "+7 999 123-45-67"])
def test_invalid_or_identifying_source_is_not_saved(draft_owner, answer):
    actor, _, _, draft = draft_owner
    with pytest.raises(ApiError) as error:
        _run(lambda sessions: _reserve(sessions, actor, draft, answer))
    assert error.value.status_code == 422
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert _run(lambda sessions: discover_answer(
        sessions, telegram_user_id=actor, draft_id=draft["id"],
    )) is None


def test_foreign_actor_cannot_read_or_cancel_answer_source(draft_owner):
    actor, _, _, draft = draft_owner
    claim = _run(lambda sessions: _reserve(sessions, actor, draft))
    other = 97_000_000_000 + uuid4().int % 100_000_000
    seed_admin(other)
    for action in (read_answer, cancel_answer):
        with pytest.raises(ApiError) as error:
            _run(lambda sessions, action=action: action(
                sessions, telegram_user_id=other, operation_id=claim.response.operation_id,
            ))
        assert error.value.status_code == 404


def test_safe_stop_allows_read_and_cancel_but_not_new_dispatch(draft_owner, monkeypatch):
    actor, _, _, draft = draft_owner
    claim = _run(lambda sessions: _reserve(sessions, actor, draft))
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    assert _run(lambda sessions: read_answer(
        sessions, telegram_user_id=actor, operation_id=claim.response.operation_id,
    )).response.state == "PENDING"
    with pytest.raises(ApiError) as error:
        _run(lambda sessions: _reserve(sessions, actor, draft, "Ещё одно описание события."))
    assert error.value.status_code == 503
    assert _run(lambda sessions: cancel_answer(
        sessions, telegram_user_id=actor, operation_id=claim.response.operation_id,
    )).response.state == "MANUAL_CONTINUATION"
