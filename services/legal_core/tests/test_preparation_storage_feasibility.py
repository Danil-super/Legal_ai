"""Intent encoding fits current storage; not a proof of runtime leases or authz."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from legal_core.case_preparation_contracts import DraftPreparationIntent
from legal_core.database import database_url
from legal_core.models import IdempotencyRecord, TelegramIntakeDraft
from test_case_api import seed_admin


def _intent_payload():
    return {
        "clinicId": "00000000-0000-0000-0000-000000000001",
        "actorMembershipId": "00000000-0000-0000-0000-000000000002",
        "draftId": "00000000-0000-0000-0000-000000000003",
        "draftRevision": 1,
        "inputSha256": "a" * 64,
        "operationKind": "UNDERSTAND_INTAKE",
    }


def test_intent_fits_existing_idempotency_identity_and_field_lengths():
    intent = DraftPreparationIntent.model_validate(_intent_payload())
    replay = DraftPreparationIntent.model_validate(intent.model_dump(by_alias=True))
    assert replay.idempotency_key == intent.idempotency_key
    assert replay.request_sha256 == intent.request_sha256
    assert len(intent.scope) <= IdempotencyRecord.__table__.c.scope.type.length
    assert len(str(intent.idempotency_key)) <= IdempotencyRecord.__table__.c.key.type.length
    assert len(intent.request_sha256) == 64
    unique_keys = [
        {column.name for column in constraint.columns}
        for constraint in IdempotencyRecord.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert {"scope", "actor_membership_id", "key"} in unique_keys


@pytest.mark.parametrize(("field", "value"), [
    ("clinicId", "00000000-0000-0000-0000-000000000004"),
    ("actorMembershipId", "00000000-0000-0000-0000-000000000004"),
    ("draftId", "00000000-0000-0000-0000-000000000004"),
    ("draftRevision", 2),
    ("inputSha256", "b" * 64),
    ("operationKind", "INTERPRET_ANSWER"),
])
def test_distinct_intents_cannot_replay_the_original(field, value):
    payload = _intent_payload()
    first = DraftPreparationIntent.model_validate(payload)
    payload[field] = value
    other = DraftPreparationIntent.model_validate(payload)
    assert other.idempotency_key != first.idempotency_key
    assert other.request_sha256 != first.request_sha256


@pytest.mark.parametrize("revision", [True, 0, -1, "1", 1.0])
def test_intent_revision_is_a_positive_strict_integer(revision):
    with pytest.raises(ValidationError):
        DraftPreparationIntent.model_validate({**_intent_payload(), "draftRevision": revision})


@pytest.mark.parametrize(("field", "value"), [
    ("operationKind", "APPROVE_LAW"), ("inputSha256", "a" * 63),
    ("inputSha256", "A" * 64), ("rawDescription", "synthetic untrusted text"),
])
def test_intent_rejects_commands_and_raw_content(field, value):
    with pytest.raises(ValidationError):
        DraftPreparationIntent.model_validate({**_intent_payload(), field: value})


def test_typed_uuid_and_wire_uuid_have_identical_intent_identity():
    payload = _intent_payload()
    wire = DraftPreparationIntent.model_validate(payload)
    payload["draftId"] = UUID(str(payload["draftId"]))
    domain = DraftPreparationIntent.model_validate(payload)
    assert domain.idempotency_key == wire.idempotency_key


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL",
)
@pytest.mark.parametrize("state", ["IN_PROGRESS", "SUCCEEDED", "FAILED"])
def test_runtime_role_can_persist_bounded_intent_without_case_or_report_and_rls_hides_it(state):
    """Actual existing storage proof, not a claim that leases/expiry already work."""
    clinic_id, actor_id = seed_admin(10_800_000_000 + uuid4().int % 1_000_000_000)
    other_clinic, _ = seed_admin(12_800_000_000 + uuid4().int % 1_000_000_000)
    intent = DraftPreparationIntent.model_validate({
        **_intent_payload(), "clinicId": clinic_id, "actorMembershipId": actor_id,
        "draftId": uuid4(),
    })
    record_id = uuid4()
    engine = create_engine(database_url())
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                            {"id": str(clinic_id)})
            assert not session.execute(text(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )).scalar_one()
            session.add(TelegramIntakeDraft(
                id=intent.draft_id, clinic_id=clinic_id, actor_membership_id=actor_id,
                wizard_state="REVIEW", draft_json={}, revision=1,
                purge_after=datetime.now(UTC) + timedelta(days=30),
            ))
            session.flush()

            def record(identity):
                return IdempotencyRecord(
                    id=identity, clinic_id=clinic_id, actor_membership_id=actor_id,
                    scope=intent.scope, key=str(intent.idempotency_key),
                    request_sha256=intent.request_sha256, state=state,
                    resource_type="INTAKE_PREPARATION", resource_id=intent.draft_id,
                    response_json={"draftRevision": 1, "inputSha256": "a" * 64, "proposals": []}
                    if state == "SUCCEEDED" else None,
                )

            session.add(record(record_id))
            session.flush()
            with pytest.raises(IntegrityError), session.begin_nested():
                session.add(record(uuid4()))
                session.flush()
        with Session(engine) as session, session.begin():
            session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                            {"id": str(clinic_id)})
            stored = session.scalar(
                select(IdempotencyRecord).where(IdempotencyRecord.id == record_id)
            )
            assert stored is not None
            assert stored.resource_id == intent.draft_id
            assert stored.request_sha256 == intent.request_sha256
            assert stored.state == state
            assert stored.response_json == (
                {"draftRevision": 1, "inputSha256": "a" * 64, "proposals": []}
                if state == "SUCCEEDED" else None
            )
        with Session(engine) as session, session.begin():
            session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                            {"id": str(other_clinic)})
            assert session.scalar(
                select(IdempotencyRecord).where(IdempotencyRecord.id == record_id)
            ) is None
    finally:
        engine.dispose()
