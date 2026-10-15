"""Additive schema checkpoint; not a claim that preparation writers are enabled."""

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from legal_core.analysis_jobs import AnalysisJob
from legal_core.database import database_url
from legal_core.models import (
    Case,
    CaseMaterial,
    IdempotencyRecord,
    TelegramCaseWorkflow,
    TelegramIntakeDraft,
)
from test_case_api import seed_admin
from test_migrations import alembic_config


def test_preparation_lease_columns_are_nullable_and_fenced_together():
    columns = IdempotencyRecord.__table__.c
    assert columns.lease_token.nullable
    assert columns.lease_until.nullable
    names = {constraint.name for constraint in IdempotencyRecord.__table__.constraints}
    assert "ck_idempotency_preparation_lease_pair" in names
    assert "ck_idempotency_preparation_lease_scope" in names


def test_workflow_receipt_version_preserves_legacy_required_report_by_constraint():
    table = TelegramCaseWorkflow.__table__
    assert table.c.report_id.nullable
    assert not table.c.receipt_version.nullable
    assert str(table.c.receipt_version.server_default.arg) == "1"
    assert "ck_telegram_workflow_receipt" in {item.name for item in table.constraints}


def test_analysis_job_result_version_defaults_to_legacy_and_keeps_case_fk():
    table = AnalysisJob.__table__
    assert not table.c.result_version.nullable
    assert str(table.c.result_version.server_default.arg) == "1"
    assert not table.c.case_id.nullable
    assert "ck_analysis_job_result_version" in {item.name for item in table.constraints}


@pytest.fixture
def runtime_storage():
    if os.getenv("POSTGRES_INTEGRATION") != "1":
        pytest.skip("requires disposable PostgreSQL")
    clinic, actor = seed_admin(14_800_000_000 + uuid4().int % 1_000_000_000)
    engine = create_engine(database_url())

    @contextmanager
    def tenant_session():
        with Session(engine, expire_on_commit=False) as session, session.begin():
            session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                            {"id": str(clinic)})
            assert not session.scalar(text(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
            ))
            yield session

    try:
        yield engine, clinic, actor, tenant_session
    finally:
        engine.dispose()


def test_expanded_database_has_tenant_fks_rls_and_legacy_defaults(runtime_storage):
    engine, _, _, _ = runtime_storage
    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("idempotency_records")} >= {
        "lease_token", "lease_until",
    }
    workflow_columns = {column["name"]: column for column in
                        inspector.get_columns("telegram_case_workflows")}
    assert workflow_columns["report_id"]["nullable"]
    assert workflow_columns["receipt_version"]["default"] == "1"
    assert any(
        item["constrained_columns"] == ["clinic_id", "case_id", "report_id"]
        and item["referred_table"] == "case_reports"
        for item in inspector.get_foreign_keys("telegram_case_workflows")
    )
    with engine.connect() as connection:
        secured = connection.execute(text(
            "SELECT relname FROM pg_class WHERE relrowsecurity AND relname IN "
            "('idempotency_records', 'telegram_case_workflows', 'analysis_jobs')"
        )).scalars().all()
        assert set(secured) == {"idempotency_records", "telegram_case_workflows", "analysis_jobs"}


@pytest.mark.parametrize(("token", "deadline", "resource", "state"), [
    (uuid4(), None, "INTAKE_PREPARATION", "IN_PROGRESS"),
    (None, datetime.now(UTC), "INTAKE_PREPARATION", "IN_PROGRESS"),
    (uuid4(), datetime.now(UTC), None, "IN_PROGRESS"),
    (uuid4(), datetime.now(UTC), "TELEGRAM_INTAKE_DRAFT", "IN_PROGRESS"),
    (uuid4(), datetime.now(UTC), "INTAKE_PREPARATION", "SUCCEEDED"),
])
def test_database_rejects_partial_or_out_of_scope_leases(
    runtime_storage, token, deadline, resource, state,
):
    _, clinic, actor, tenant_session = runtime_storage
    with tenant_session() as session, pytest.raises(IntegrityError), session.begin_nested():
        session.add(IdempotencyRecord(
            clinic_id=clinic, actor_membership_id=actor, scope="synthetic-storage-check",
            key=str(uuid4()), request_sha256="a" * 64, resource_type=resource,
            resource_id=uuid4(), state=state, lease_token=token, lease_until=deadline,
        ))
        session.flush()


@pytest.mark.parametrize("version", [0, 1, 3])
def test_report_free_workflow_requires_explicit_version_two(runtime_storage, version):
    _, clinic, actor, tenant_session = runtime_storage
    with tenant_session() as session:
        case = Case(clinic_id=clinic, created_by_membership_id=actor)
        session.add(case)
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(TelegramCaseWorkflow(
                id=uuid4(), clinic_id=clinic, actor_membership_id=actor, case_id=case.id,
                request_sha256="b" * 64, state="SUCCEEDED", report_id=None,
                receipt_version=version,
            ))
            session.flush()


def test_explicit_v2_receipt_does_not_require_a_placeholder_report(runtime_storage):
    _, clinic, actor, tenant_session = runtime_storage
    with tenant_session() as session:
        case = Case(clinic_id=clinic, created_by_membership_id=actor)
        session.add(case)
        session.flush()
        receipt = TelegramCaseWorkflow(
            id=uuid4(), clinic_id=clinic, actor_membership_id=actor, case_id=case.id,
            request_sha256="b" * 64, state="SUCCEEDED", report_id=None, receipt_version=2,
        )
        session.add(receipt)
        session.flush()
        assert receipt.report_id is None
        assert session.scalar(text(
            "SELECT count(*) FROM case_reports WHERE clinic_id = :clinic AND case_id = :case"
        ), {"clinic": clinic, "case": case.id}) == 0


@pytest.mark.parametrize("version", [0, 1, 2, 3])
def test_job_result_version_is_explicit_but_legacy_default_stays_valid(runtime_storage, version):
    _, clinic, actor, tenant_session = runtime_storage
    with tenant_session() as session:
        case = Case(clinic_id=clinic, created_by_membership_id=actor)
        session.add(case)
        session.flush()
        job = AnalysisJob(
            clinic_id=clinic, case_id=case.id, actor_membership_id=actor,
            message_id=1, result_version=version,
        )
        if version in {1, 2}:
            session.add(job)
            session.flush()
            assert job.result_version == version
        else:
            with pytest.raises(IntegrityError), session.begin_nested():
                session.add(job)
                session.flush()


@pytest.mark.parametrize("state", ["IN_PROGRESS", "SUCCEEDED", "FAILED"])
@pytest.mark.parametrize("owner_state", ["EXPIRED", "ARCHIVED", "SUBMITTED", "MISSING", "LIVE"])
def test_retention_scrubs_preparation_in_all_states_and_preserves_tombstones(
    runtime_storage, state, owner_state,
):
    _, clinic, actor, tenant_session = runtime_storage
    draft_id, record_id, token = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    with tenant_session() as session:
        if owner_state != "MISSING":
            session.add(TelegramIntakeDraft(
                id=draft_id, clinic_id=clinic, actor_membership_id=actor,
                status=owner_state if owner_state in {"ARCHIVED", "SUBMITTED"} else "DRAFT",
                wizard_state="REVIEW", draft_json={}, revision=1,
                purge_after=now + timedelta(days=-1 if owner_state == "EXPIRED" else 1),
            ))
            session.flush()
            if owner_state == "EXPIRED":
                # A metadata-only synthetic material holds the parent DELETE gate closed.
                # There are no patient documents or actual storage objects in this fixture.
                session.add(CaseMaterial(
                    clinic_id=clinic, draft_id=draft_id, uploader_membership_id=actor,
                    display_name="synthetic.txt", mime_type="text/plain", raw_size_bytes=1,
                    raw_sha256="d" * 64, raw_object_key=f"synthetic/{uuid4()}",
                    expires_at=now + timedelta(days=1),
                ))
                session.flush()
        session.add(IdempotencyRecord(
            id=record_id, clinic_id=clinic, actor_membership_id=actor,
            scope=f"draft-prep:v1:{draft_id}", key=str(uuid4()), request_sha256="c" * 64,
            resource_type="INTAKE_PREPARATION", resource_id=draft_id, state=state,
            response_json={"proposals": []},
            lease_token=token if state == "IN_PROGRESS" else None,
            lease_until=now + timedelta(minutes=3) if state == "IN_PROGRESS" else None,
        ))
    with tenant_session() as session:
        session.execute(text("SELECT public.purge_expired_telegram_intake_drafts()"))
    with tenant_session() as session:
        record = session.get(IdempotencyRecord, record_id)
        assert record is not None
        assert record.request_sha256 == "c" * 64
        assert record.resource_id == draft_id
        if owner_state == "LIVE":
            assert record.response_json == {"proposals": []}
            assert record.state == state
            assert record.lease_token == (token if state == "IN_PROGRESS" else None)
        else:
            assert record.response_json is None
            assert record.lease_token is None
            assert record.lease_until is None
            assert record.state == ("FAILED" if state == "IN_PROGRESS" else state)
        if owner_state == "EXPIRED":
            assert session.get(TelegramIntakeDraft, draft_id) is not None


def test_downgrade_refuses_to_erase_retained_preparation_records(runtime_storage):
    _, clinic, actor, tenant_session = runtime_storage
    with tenant_session() as session:
        session.add(IdempotencyRecord(
            clinic_id=clinic, actor_membership_id=actor, scope="synthetic-downgrade-check",
            key=str(uuid4()), request_sha256="a" * 64, state="FAILED",
            resource_type="INTAKE_PREPARATION", resource_id=uuid4(), response_json=None,
        ))
    with pytest.raises(ProgrammingError, match="cannot downgrade"):
        command.downgrade(alembic_config(), "c4a0c3f6e852")
    with tenant_session() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "d10a6b3e7f91"
