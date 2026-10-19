"""Private answer lifecycle on disposable PostgreSQL; no provider or patient data."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.case_api import ApiError, _canonical_hash
from legal_core.database import database_url, owner_database_url
from legal_core.intake_answer_contracts import AnswerInterpretation
from legal_core.intake_answer_lifecycle import complete_answer, fail_answer, read_answer_context
from legal_core.intake_answer_operations import cancel_answer, read_answer, reserve_answer
from legal_core.intake_preparation_contracts import SourceSpan
from legal_core.models import AuditEvent, IdempotencyRecord
from test_case_api import actor_headers, application_client, seed_admin

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
ANSWER = "Это письменная претензия"
SUMMARY = "Пациент сообщил о сколе коронки после лечения."


@pytest.fixture
def owner():
    actor = 31_800_000_000 + uuid4().int % 100_000_000
    clinic, membership = seed_admin(actor)
    with application_client() as client:
        created = client.post("/v1/telegram-intake-drafts", headers=actor_headers(actor, uuid4()),
                              json={"intakeSchemaVersion": "dental-case-intake.v2"})
        assert created.status_code == 201, created.text
        draft = created.json()
        saved = client.put(f"/v1/telegram-intake-drafts/{draft['id']}",
                           headers=actor_headers(actor, uuid4()), json={
                               "expectedRevision": draft["revision"], "wizardState": "INCOMING",
                               "draftData": {"intakeVersion": 2, "eventSummary": SUMMARY},
                           })
        assert saved.status_code == 200, saved.text
        draft = saved.json()
    return actor, clinic, membership, draft


def run(operation):
    async def invoke():
        engine = create_async_engine(database_url())
        try:
            return await operation(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()
    return asyncio.run(invoke())


def reserve(owner, *, answer=ANSWER):
    actor, _, _, draft = owner
    return run(lambda sessions: reserve_answer(
        sessions, telegram_user_id=actor, draft_id=draft["id"],
        expected_revision=draft["revision"], answer=answer,
    ))


def interpretation(value="FORMAL_DOCUMENT", *, source=ANSWER):
    return AnswerInterpretation.model_validate({
        "status": "PROPOSED", "value": value,
        "sourceSpans": [{"start": 0, "end": len(source)}],
    })


def context(owner, lease, **change):
    actor, _, _, draft = owner
    arguments = {"telegram_user_id": actor, "operation_id": lease.response.operation_id,
                 "lease_token": lease.lease_token, "expected_revision": draft["revision"],
                 "expected_input_sha256": lease.intent.input_sha256} | change
    return run(lambda sessions: read_answer_context(sessions, **arguments))


def complete(owner, lease, value=None, **change):
    arguments = {"telegram_user_id": owner[0], "operation_id": lease.response.operation_id,
                 "lease_token": lease.lease_token,
                 "interpretation": value or interpretation()} | change
    return run(lambda sessions: complete_answer(sessions, **arguments))


def fail(owner, lease, **change):
    arguments = {"telegram_user_id": owner[0], "operation_id": lease.response.operation_id,
                 "lease_token": lease.lease_token, "reason_code": "MODEL_UNAVAILABLE"} | change
    return run(lambda sessions: fail_answer(sessions, **arguments))


def mutate(statement, parameters):
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text(statement), parameters)
    finally:
        engine.dispose()


def save_data(owner, change, *, state="INCOMING"):
    actor, clinic, membership, draft = owner
    with application_client() as client:
        saved = client.put(f"/v1/telegram-intake-drafts/{draft['id']}",
                           headers=actor_headers(actor, uuid4()), json={
                               "expectedRevision": draft["revision"], "wizardState": state,
                               "draftData": draft["draftData"] | change,
                           })
        assert saved.status_code == 200, saved.text
    return actor, clinic, membership, saved.json()


def test_private_context_is_bound_only_to_live_owned_answer_and_releases_locks(owner):
    lease = reserve(owner)
    result = context(owner, lease)
    assert result.operation_id == lease.response.operation_id
    assert str(result.draft_id) == owner[3]["id"] and result.draft_revision == owner[3]["revision"]
    assert result.answer == ANSWER
    assert result.answer_sha256 == _canonical_hash({"answer": ANSWER})
    assert result.question_sha256 == result.question.question_sha256
    assert result.confirmed_context == {"intakeVersion": 2, "eventSummary": SUMMARY}
    assert result.as_of_date == lease.intent.as_of_date
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '250ms'"))
            assert connection.scalar(text(
                "SELECT id FROM idempotency_records WHERE id=:id FOR UPDATE NOWAIT"
            ), {"id": lease.response.operation_id}) == lease.response.operation_id
    finally:
        engine.dispose()


def test_completion_persists_one_immutable_proposal_without_confirming_or_creating_case(owner):
    lease = reserve(owner)
    result = complete(owner, lease)
    resumed = run(lambda sessions: read_answer(
        sessions, telegram_user_id=owner[0], operation_id=lease.response.operation_id,
    ))
    assert result.response == resumed.response
    assert result.response.state == "PROPOSED"
    assert result.response.interpretation.value == "FORMAL_DOCUMENT"
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            record = connection.execute(select(IdempotencyRecord).where(
                IdempotencyRecord.id == lease.response.operation_id,
            )).mappings().one()
            assert record["state"] == "SUCCEEDED" and record["lease_token"] is None
            assert record["lease_until"] is None
            audits = connection.execute(select(AuditEvent.metadata_json).where(
                AuditEvent.resource_id == lease.response.operation_id,
                AuditEvent.action == "INTAKE_ANSWER_PROPOSED",
            )).scalars().all()
            assert audits == [{
                "draftId": owner[3]["id"], "revision": owner[3]["revision"],
                "inputSha256": lease.intent.input_sha256,
                "intentSha256": lease.intent.request_sha256,
                "questionSha256": lease.intent.question_sha256,
                "answerSha256": lease.intent.answer_sha256,
                "proposalSha256": _canonical_hash(interpretation().model_dump(
                    mode="json", by_alias=True,
                )),
            }]
            assert ANSWER not in json.dumps(audits, ensure_ascii=False)
            assert connection.scalar(text("SELECT count(*) FROM cases WHERE clinic_id=:clinic"),
                                     {"clinic": owner[1]}) == 0
    finally:
        engine.dispose()
    with application_client() as client:
        actual = client.get(f"/v1/telegram-intake-drafts/{owner[3]['id']}",
                            headers=actor_headers(owner[0])).json()
    assert actual["draftData"] == owner[3]["draftData"]
    assert actual["revision"] == owner[3]["revision"]


@pytest.mark.parametrize("reason", ["AMBIGUOUS", "CONTRADICTS_CONFIRMED", "OFF_QUESTION"])
def test_unresolved_survives_restart_without_inventing_unknown(owner, reason):
    lease = reserve(owner)
    value = AnswerInterpretation.model_validate({
        "status": "UNRESOLVED", "value": None, "sourceSpans": [], "reasonCode": reason,
    })
    result = complete(owner, lease, value)
    assert result.response.state == "UNRESOLVED"
    assert result.response.interpretation.value is None
    assert result.response.interpretation.reason_code == reason


@pytest.mark.parametrize("operation", [context, complete, fail])
def test_wrong_lease_never_reads_completes_or_destroys_pending_answer(owner, operation):
    lease = reserve(owner)
    with pytest.raises(ApiError) as caught:
        operation(owner, lease, lease_token=uuid4())
    assert caught.value.status_code == 409
    assert run(lambda sessions: read_answer(
        sessions, telegram_user_id=owner[0], operation_id=lease.response.operation_id,
    )).response.state == "PENDING"


@pytest.mark.parametrize("operation", [context, complete, fail])
@pytest.mark.parametrize("access", ["revoked", "suspended", "expired", "other_creator"])
def test_context_completion_and_failure_require_current_access(owner, operation, access):
    lease = reserve(owner)
    caller = owner[0]
    if access == "revoked":
        mutate("UPDATE clinic_users SET status='REVOKED' WHERE id=:id", {"id": owner[2]})
    elif access == "suspended":
        mutate("UPDATE subscription_entitlements SET status='SUSPENDED' WHERE clinic_id=:id",
               {"id": owner[1]})
    elif access == "expired":
        mutate("UPDATE telegram_intake_drafts SET purge_after=now()-interval '1 minute' "
               "WHERE id=:id",
               {"id": owner[3]["id"]})
    else:
        caller += 2_000_000_000
        seed_admin(caller)
    with pytest.raises(ApiError) as caught:
        operation(owner, lease, telegram_user_id=caller)
    assert caught.value.status_code in {403, 404, 410}
    assert caught.value.__context__ is None


@pytest.mark.parametrize("operation", [context, complete, fail])
@pytest.mark.parametrize("change", ["revision", "state", "source", "question"])
def test_late_lifecycle_is_stale_after_navigation_or_draft_edit(owner, operation, change):
    lease = reserve(owner)
    changes = {"revision": "revision=revision+1", "state": "wizard_state='SITUATION'",
               "source": "draft_json=jsonb_set(draft_json,'{eventSummary}','\"Другой факт.\"')",
               "question": "draft_json=jsonb_set(draft_json,'{incomingKind}','\"OTHER\"')"}
    mutate(f"UPDATE telegram_intake_drafts SET {changes[change]} WHERE id=:id",
           {"id": owner[3]["id"]})
    with pytest.raises(ApiError) as caught:
        operation(owner, lease)
    assert caught.value.status_code == 409


@pytest.mark.parametrize("operation", [context, complete, fail])
def test_durable_cancel_wins_against_late_context_completion_or_failure(owner, operation):
    lease = reserve(owner)
    run(lambda sessions: cancel_answer(sessions, telegram_user_id=owner[0],
                                      operation_id=lease.response.operation_id))
    with pytest.raises(ApiError) as caught:
        operation(owner, lease)
    assert caught.value.status_code == 409


def test_safe_stop_blocks_context_but_failure_can_preserve_manual_continuation(owner, monkeypatch):
    lease = reserve(owner)
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    with pytest.raises(ApiError) as caught:
        context(owner, lease)
    assert caught.value.code == "ANALYSIS_SAFE_STOP"
    assert fail(owner, lease).response.state == "MANUAL_CONTINUATION"


@pytest.mark.parametrize("value", [True, "SYNTHETIC_PRIVATE_DETAIL", {"riskLevel": "LOW"}])
def test_model_value_is_rechecked_without_raw_validation_error(owner, value):
    lease = reserve(owner)
    with pytest.raises(ApiError) as caught:
        complete(owner, lease, interpretation(value))
    assert caught.value.status_code == 422
    assert "SYNTHETIC_PRIVATE_DETAIL" not in repr(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize("reason", ["MODEL_TIMEOUT", "MODEL_UNAVAILABLE", "MODEL_PROTOCOL_INVALID"])
def test_closed_failure_reason_is_durable_and_has_no_source_in_audit(owner, reason):
    lease = reserve(owner)
    assert fail(owner, lease, reason_code=reason).response.state == "MANUAL_CONTINUATION"
    with pytest.raises(ApiError):
        complete(owner, lease)


def test_arbitrary_failure_reason_cannot_enter_audit(owner):
    lease = reserve(owner)
    with pytest.raises(ValueError) as caught:
        fail(owner, lease, reason_code="SYNTHETIC_PRIVATE_DETAIL")
    assert "SYNTHETIC_PRIVATE_DETAIL" not in repr(caught.value)


def test_expired_lease_refuses_context_and_completion(owner):
    lease = reserve(owner)
    mutate("UPDATE idempotency_records SET lease_until=:at WHERE id=:id",
           {"at": datetime.now(UTC) - timedelta(seconds=1), "id": lease.response.operation_id})
    for operation in (context, complete, fail):
        with pytest.raises(ApiError) as caught:
            operation(owner, lease)
        assert caught.value.status_code == 409


def test_confirmed_context_keeps_factual_notes_but_omits_origins_and_clarification_routing(owner):
    owner = save_data(owner, {
        "clinicActionsNote": "Пригласили пациента на осмотр.",
        "incomingSourceStatus": "FILE_ATTACHED", "caseMaterialsStatus": "ATTACHED",
        "eventDate": {"date": None, "precision": "UNKNOWN"},
        "analysisClarificationRequests": [{"questionId": "RESPONSE_PURPOSE"}],
    })
    # Owner-only fixture setup represents an existing bound clarification origin. The public
    # PUT correctly forbids adding/changing that origin and must not be weakened for this test.
    mutate("UPDATE telegram_intake_drafts SET draft_json=jsonb_set(draft_json,"
           "'{correctionSource}',CAST(:origin AS jsonb)) WHERE id=:id", {
               "id": owner[3]["id"], "origin": json.dumps({
                   "caseId": str(uuid4()), "factSnapshotSha256": "c" * 64,
               }),
           })
    result = context(owner, reserve(owner))
    assert result.confirmed_context["clinicActionsNote"] == "Пригласили пациента на осмотр."
    assert result.confirmed_context["incomingSourceStatus"] == "FILE_ATTACHED"
    assert result.confirmed_context["eventDate"] == {"date": None, "precision": "UNKNOWN"}
    assert "correctionSource" not in result.confirmed_context
    assert "analysisClarificationRequests" not in result.confirmed_context
    assert "leaseToken" not in result.model_dump(by_alias=True)


@pytest.mark.parametrize("change", [
    {"eventSummary": "Контакт patient@example.invalid для ответа"},
    {"clinicActionsNote": "Контакт patient@example.invalid для ответа"},
    {"caseMaterialsStatus": {"fileText": "SYNTHETIC_PRIVATE_DOCUMENT"}},
    {"incomingSourceStatus": {"storageKey": "SYNTHETIC_PRIVATE_DOCUMENT"}},
    {"clinicActionsNote": "x" * 501}, {"incomingKind": "SYNTHETIC_INVALID_ENUM"},
])
def test_context_refuses_identifiers_and_disguised_files_in_factual_keys(owner, change):
    owner = save_data(owner, change)
    lease = reserve(owner)
    with pytest.raises(ApiError) as caught:
        context(owner, lease)
    assert caught.value.status_code == 422 and caught.value.code == "ANSWER_CONTEXT_INVALID"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "SYNTHETIC_PRIVATE_DOCUMENT" not in repr(caught.value)


def test_unknown_date_stays_an_attempt_with_required_null_after_completion_and_restart(owner):
    owner = save_data(owner, {}, state="EVENT_DATE")
    source = "Не знаю дату"
    lease = reserve(owner, answer=source)
    value = interpretation({"date": None, "precision": "UNKNOWN"}, source=source)
    completed = complete(owner, lease, value)
    resumed = run(lambda sessions: read_answer(
        sessions, telegram_user_id=owner[0], operation_id=lease.response.operation_id,
    ))
    assert completed.response.state == resumed.response.state == "PROPOSED"
    assert resumed.response.interpretation.value == {"date": None, "precision": "UNKNOWN"}


def test_constructed_nested_bool_anchor_cannot_bypass_completion_validation(owner):
    lease = reserve(owner)
    forged = AnswerInterpretation.model_construct(
        status="PROPOSED", value="FORMAL_DOCUMENT", reason_code=None,
        source_spans=[SourceSpan.model_construct(start=True, end=3)],
    )
    with pytest.raises(ApiError) as caught:
        complete(owner, lease, forged)
    assert caught.value.status_code == 422 and caught.value.__context__ is None


def test_mutable_cached_proposal_cannot_replace_immutable_proposal_audit(owner):
    lease = reserve(owner)
    complete(owner, lease)
    mutate("UPDATE idempotency_records SET response_json=jsonb_set(response_json,"
           "'{interpretation,value}','\"OTHER\"') WHERE id=:id",
           {"id": lease.response.operation_id})
    with pytest.raises(ApiError) as caught:
        run(lambda sessions: read_answer(
            sessions, telegram_user_id=owner[0], operation_id=lease.response.operation_id,
        ))
    assert caught.value.code == "ANSWER_PROPOSAL_INVALID"


def test_context_rejects_wrong_revision_input_hash_and_strict_boolean_revision(owner):
    lease = reserve(owner)
    for change in ({"expected_revision": True}, {"expected_revision": owner[3]["revision"] + 1},
                   {"expected_input_sha256": "b" * 64}):
        with pytest.raises(ApiError) as caught:
            context(owner, lease, **change)
        assert caught.value.status_code == 409 and caught.value.code == "ANSWER_INPUT_STALE"
