"""Actual cache constraints; not a draft producer or proof of endpoint authorization."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from legal_core.case_preparation_contracts import InternalWorkProduct
from legal_core.database import database_url, owner_database_url
from legal_core.models import AuditEvent, Case, IdempotencyRecord
from test_ai_first_contracts import work_product
from test_case_api import seed_admin

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL",
)


@pytest.fixture
def runtime_storage():
    clinic, actor = seed_admin(20_000_000_000 + uuid4().int % 1_000_000_000)
    other_clinic, _ = seed_admin(22_000_000_000 + uuid4().int % 1_000_000_000)
    second_actor, second_user, case_id = uuid4(), uuid4(), uuid4()
    owner = create_engine(owner_database_url())
    engine = create_engine(database_url())
    try:
        with owner.begin() as connection:
            connection.execute(text(
                "INSERT INTO users(id,telegram_user_id) VALUES (:id,:telegram)"
            ), {"id": second_user, "telegram": 24_000_000_000 + uuid4().int % 1_000_000_000})
            connection.execute(text(
                "INSERT INTO clinic_users(id,clinic_id,user_id,role) "
                "VALUES (:id,:clinic,:user,'CLINIC_LAWYER')"
            ), {"id": second_actor, "clinic": clinic, "user": second_user})
        with Session(engine) as session, session.begin():
            session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                            {"id": str(clinic)})
            assert not session.execute(text(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )).scalar_one()
            session.add(Case(id=case_id, clinic_id=clinic, created_by_membership_id=actor))
        yield engine, clinic, other_clinic, actor, second_actor, case_id
    finally:
        engine.dispose()
        owner.dispose()


def cached_version(clinic, actor, case_id, *, lease=False):
    payload = InternalWorkProduct.model_validate(work_product(
        clinicId=str(clinic), source={
            "operationId": str(uuid4()), "caseId": str(case_id),
            "factSnapshotSha256": "a" * 64, "context": None,
        },
    )).model_dump(mode="json", by_alias=True)
    return IdempotencyRecord(
        id=uuid4(), clinic_id=clinic, actor_membership_id=actor,
        scope=f"ai-first-feasibility:{case_id}", key="version-1", request_sha256="a" * 64,
        state="IN_PROGRESS" if lease else "SUCCEEDED", resource_type="AI_WORK_PRODUCT",
        resource_id=case_id, response_json=payload, lease_token=uuid4() if lease else None,
        lease_until=datetime.now(UTC) + timedelta(seconds=30) if lease else None,
    )


def test_cache_fits_json_and_is_tenant_hidden_but_version_identity_is_actor_local(runtime_storage):
    engine, clinic, other, actor, second_actor, case_id = runtime_storage
    first = cached_version(clinic, actor, case_id)
    expected = first.response_json
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                        {"id": str(clinic)})
        session.add(first)
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(cached_version(clinic, actor, case_id))
            session.flush()
        # This is valid cache behavior, but cannot alone guarantee a global version ledger.
        second = cached_version(clinic, second_actor, case_id)
        second.response_json = expected
        session.add(second)
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                        {"id": str(clinic)})
        versions = session.scalars(select(IdempotencyRecord).where(
            IdempotencyRecord.resource_id == case_id,
        )).all()
        assert len(versions) == 2
        assert all(item.response_json == expected for item in versions)
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                        {"id": str(other)})
        assert not session.scalars(select(IdempotencyRecord).where(
            IdempotencyRecord.resource_id == case_id,
        )).all()


def test_existing_preparation_lease_cannot_be_relabelled_as_work_product_lease(runtime_storage):
    engine, clinic, _, actor, _, case_id = runtime_storage
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                        {"id": str(clinic)})
        with pytest.raises(IntegrityError) as caught, session.begin_nested():
            session.add(cached_version(clinic, actor, case_id, lease=True))
            session.flush()
        assert caught.value.orig.diag.constraint_name == "ck_idempotency_preparation_lease_scope"


def test_case_linked_cache_is_purged_with_case_content_not_left_as_hidden_copy(runtime_storage):
    engine, clinic, _, actor, _, case_id = runtime_storage
    cached = cached_version(clinic, actor, case_id)
    audit_id = uuid4()
    metadata = {"contentSha256": hashlib.sha256(json.dumps(
        cached.response_json, sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest(), "version": 1}
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                        {"id": str(clinic)})
        case = session.get(Case, case_id)
        case.status = "CLOSED"
        case.closed_at = datetime.now(UTC) - timedelta(days=366)
        case.retention_due_at = datetime.now(UTC) - timedelta(seconds=1)
        session.add(cached)
        session.add(AuditEvent(
            id=audit_id, clinic_id=clinic, actor_membership_id=actor,
            action="AI_FIRST_STORAGE_PROBE", resource_type="AI_WORK_PRODUCT",
            resource_id=case_id, metadata_json=metadata, correlation_id=uuid4(),
        ))
    with Session(engine) as session, session.begin():
        assert session.execute(text("SELECT public.purge_expired_case_content()")).scalar_one() >= 1
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                        {"id": str(clinic)})
        assert session.get(Case, case_id).status == "CONTENT_PURGED"
        assert not session.scalars(select(IdempotencyRecord).where(
            IdempotencyRecord.resource_id == case_id,
        )).all()
        # Content is gone; existing audit can retain identifiers/hashes without case text.
        assert session.get(AuditEvent, audit_id).metadata_json == metadata


def test_existing_cache_payload_can_change_and_is_not_an_immutable_version_ledger(runtime_storage):
    engine, clinic, _, actor, _, case_id = runtime_storage
    cached = cached_version(clinic, actor, case_id)
    identity = cached.id
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                        {"id": str(clinic)})
        session.add(cached)
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                        {"id": str(clinic)})
        stored = session.get(IdempotencyRecord, identity)
        stored.response_json = {"version": 1, "summary": "Изменённый синтетический план."}
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                        {"id": str(clinic)})
        assert session.get(IdempotencyRecord, identity).response_json["summary"] == (
            "Изменённый синтетический план."
        )
