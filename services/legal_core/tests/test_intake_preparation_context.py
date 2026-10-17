"""Only a live, owned understanding lease may release the bounded model input."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.case_api import ApiError
from legal_core.database import owner_database_url
import legal_core.intake_preparation as preparation
from test_case_api import seed_admin
from test_intake_preparation_lifecycle import SOURCE, _reserve, _run, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]


def _binding(lease):
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            return connection.scalar(text(
                "SELECT response_json->'intent' FROM idempotency_records WHERE id=:id"
            ), {"id": lease.operation_id})
    finally:
        engine.dispose()


def _context(sessions, actor, lease, intent, **changes):
    kwargs = {
        "telegram_user_id": actor, "operation_id": lease.operation_id,
        "lease_token": lease.lease_token, "expected_revision": intent["draftRevision"],
        "expected_input_sha256": intent["inputSha256"],
    }
    kwargs.update(changes)
    return preparation.read_understanding_context(sessions, **kwargs)


def test_context_is_exact_minimal_and_releases_all_locks_before_model_io(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    intent = _binding(lease)
    result = _run(lambda sessions: _context(sessions, actor, lease, intent))
    assert result.model_dump(mode="json", by_alias=True) == {
        "operationId": str(lease.operation_id), "draftId": draft["id"],
        "draftRevision": draft["revision"], "inputSha256": intent["inputSha256"],
        "operationKind": "UNDERSTAND_INTAKE", "source": SOURCE,
    }
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '250ms'"))
            connection.execute(text(
                "SELECT id FROM telegram_intake_drafts WHERE id=:id FOR UPDATE NOWAIT"
            ), {"id": draft["id"]})
            assert connection.scalar(text(
                "SELECT state FROM idempotency_records WHERE id=:id FOR UPDATE NOWAIT"
            ), {"id": lease.operation_id}) == "IN_PROGRESS"
    finally:
        engine.dispose()


@pytest.mark.parametrize("change", [
    {"lease_token": uuid4()}, {"expected_revision": 999}, {"expected_revision": True},
    {"expected_input_sha256": "0" * 64}, {"operation_id": uuid4()},
])
def test_context_rejects_substituted_caller_metadata(draft_owner, change):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    with pytest.raises(ApiError):
        _run(lambda sessions: _context(sessions, actor, lease, _binding(lease), **change))


@pytest.mark.parametrize("assignment", [
    "lease_until=clock_timestamp()-interval '1 second'",
    "state='SUCCEEDED', lease_token=NULL, lease_until=NULL",
    "state='FAILED', lease_token=NULL, lease_until=NULL",
    "response_json=NULL",
])
def test_context_never_releases_source_after_completion_failure_or_expiry(draft_owner, assignment):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    intent = _binding(lease)
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text(
                f"UPDATE idempotency_records SET {assignment} WHERE id=:id"
            ), {"id": lease.operation_id})
        with pytest.raises(ApiError):
            _run(lambda sessions: _context(sessions, actor, lease, intent))
    finally:
        engine.dispose()


@pytest.mark.parametrize("assignment", [
    "revision=revision+1", "draft_json='{}'::jsonb", "status='ARCHIVED'",
    "status='SUBMITTED'", "purge_after=clock_timestamp()-interval '1 second'",
])
def test_context_rejects_changed_or_closed_draft(draft_owner, assignment):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    intent = _binding(lease)
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text(
                f"UPDATE telegram_intake_drafts SET {assignment} WHERE id=:id"
            ), {"id": draft["id"]})
        with pytest.raises(ApiError):
            _run(lambda sessions: _context(sessions, actor, lease, intent))
    finally:
        engine.dispose()


def test_another_active_owner_cannot_read_source_even_with_valid_lease(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    other = 19_800_000_000 + uuid4().int % 100_000_000
    seed_admin(other)
    with pytest.raises(ApiError) as error:
        _run(lambda sessions: _context(sessions, other, lease, _binding(lease)))
    assert error.value.status_code == 404


@pytest.mark.parametrize("withdraw", ["access", "lease", "safe_stop"])
def test_context_rechecks_fences_after_lock_wait(draft_owner, monkeypatch, withdraw):
    actor, clinic, _, draft = draft_owner
    lease = _run(lambda sessions: _reserve(sessions, actor, draft))
    intent = _binding(lease)
    original = preparation._draft
    engine = create_engine(owner_database_url())

    async def delayed(*args, **kwargs):
        result = await original(*args, **kwargs)
        if withdraw == "safe_stop":
            monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
        else:
            with engine.begin() as connection:
                if withdraw == "access":
                    connection.execute(text(
                        "UPDATE subscription_entitlements SET status='SUSPENDED' "
                        "WHERE clinic_id=:id"
                    ), {"id": clinic})
                else:
                    connection.execute(text(
                        "UPDATE idempotency_records SET lease_until=clock_timestamp() "
                        "-interval '1 second' WHERE id=:id"
                    ), {"id": lease.operation_id})
        return result

    monkeypatch.setattr(preparation, "_draft", delayed)
    try:
        with pytest.raises(ApiError):
            _run(lambda sessions: _context(sessions, actor, lease, intent))
    finally:
        engine.dispose()


def test_answer_interpretation_cannot_be_misrouted_as_initial_understanding(draft_owner):
    actor, _, _, draft = draft_owner
    lease = _run(lambda sessions: preparation.reserve_preparation(
        sessions, telegram_user_id=actor, draft_id=draft["id"],
        expected_revision=draft["revision"], operation_kind="INTERPRET_ANSWER",
    ))
    with pytest.raises(ApiError) as error:
        _run(lambda sessions: _context(sessions, actor, lease, _binding(lease)))
    assert error.value.code == "PREPARATION_KIND_UNSUPPORTED"


def test_context_never_releases_a_long_direct_identifier(draft_owner):
    actor, _, _, draft = draft_owner
    # Fictional identifier; the old obvious-identifier guard misses this category.
    source = "Описание тестовой ситуации, идентификатор 1234567890123456."
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE telegram_intake_drafts SET draft_json=jsonb_set("
                "draft_json,'{eventSummary}',to_jsonb(CAST(:source AS text))) WHERE id=:id"
            ), {"source": source, "id": draft["id"]})
        lease = _run(lambda sessions: _reserve(sessions, actor, draft))
        with pytest.raises(ApiError) as error:
            _run(lambda sessions: _context(sessions, actor, lease, _binding(lease)))
        assert error.value.code == "UNDERSTANDING_INPUT_INVALID"
        assert source not in str(error.value)
    finally:
        engine.dispose()
