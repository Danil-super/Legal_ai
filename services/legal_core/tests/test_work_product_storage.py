"""Immutable tenant-scoped work-product versions; no producer or legal approval API."""

import hashlib
import json
import os
import time
from importlib.util import module_from_spec, spec_from_file_location
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from legal_core.case_preparation_contracts import InternalWorkProduct
from legal_core.database import database_url, owner_database_url
from legal_core.models import AuditEvent, Case
from legal_core.runtime_db_role import provision_runtime_role
from legal_core.work_product_storage import CaseWorkProductVersion
from test_ai_first_contracts import work_product
from test_case_api import seed_admin

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.fixture
def storage():
    clinic, actor = seed_admin(44_000_000_000 + uuid4().int % 1_000_000_000)
    foreign_clinic, foreign_actor = seed_admin(46_000_000_000 + uuid4().int % 1_000_000_000)
    other_actor, other_user, case_id, second_case, foreign_case = (uuid4() for _ in range(5))
    owner = create_engine(owner_database_url())
    engine = create_engine(database_url())
    try:
        with owner.begin() as connection:
            connection.execute(text("INSERT INTO users(id,telegram_user_id) VALUES(:id,:telegram)"),
                               {"id": other_user,
                                "telegram": 48_000_000_000 + uuid4().int % 1_000_000_000})
            connection.execute(text("INSERT INTO clinic_users(id,clinic_id,user_id,role) "
                                    "VALUES(:id,:clinic,:user,'CLINIC_LAWYER')"),
                               {"id": other_actor, "clinic": clinic, "user": other_user})
        with Session(engine) as session, session.begin():
            tenant(session, clinic)
            assert not session.scalar(text(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ))
            session.add_all([Case(id=identity, clinic_id=clinic, created_by_membership_id=actor)
                             for identity in (case_id, second_case)])
        with Session(engine) as session, session.begin():
            tenant(session, foreign_clinic)
            session.add(Case(id=foreign_case, clinic_id=foreign_clinic,
                             created_by_membership_id=foreign_actor))
        yield (engine, owner, clinic, actor, other_actor, case_id, second_case,
               foreign_clinic, foreign_actor, foreign_case)
    finally:
        # Expiration/race probes must not leave due cases for another test's
        # global retention-count assertion. Only this fixture's identities.
        with owner.begin() as connection:
            connection.execute(text("UPDATE cases SET retention_due_at=NULL "
                                    "WHERE id IN (:first,:second,:foreign)"), {
                "first": case_id, "second": second_case, "foreign": foreign_case,
            })
        engine.dispose()
        owner.dispose()


def tenant(session, clinic):
    session.execute(text("SELECT set_config('app.current_clinic_id', :id, true)"),
                    {"id": str(clinic)})


def version(clinic, actor, case_id, *, product=None, number=1, context=False, **change):
    operation, product = uuid4(), product or uuid4()
    payload = work_product(
        workProductId=str(product), version=number,
        parentVersion=number - 1 if number > 1 else None, clinicId=str(clinic),
        source={"operationId": str(operation), "caseId": str(case_id),
                "factSnapshotSha256": "a" * 64, "context": {
                    "asOfDate": "2026-10-26", "riskPolicyVersion": "dental-risk.v4",
                    "evidenceTraceSha256": "b" * 64,
                    "clinicDocumentContextTraceSha256": "c" * 64,
                } if context else None},
    )
    parsed = InternalWorkProduct.model_validate(payload)
    values = dict(
        id=uuid4(), clinic_id=clinic, case_id=case_id, work_product_id=product,
        version=number, parent_version=number - 1 if number > 1 else None,
        origin="AI", review_state="UNAPPROVED", author_membership_id=actor,
        reviewed_by_membership_id=None, reviewed_at=None,
        schema_version=parsed.schema_version,
        payload_json=parsed.model_dump(mode="json", by_alias=True),
        content_sha256=parsed.content_sha256, source_operation_id=operation,
        fact_snapshot_sha256="a" * 64,
        evidence_trace_sha256="b" * 64 if context else None,
        clinic_document_context_trace_sha256="c" * 64 if context else None,
        reference_example_trace_sha256=None, risk_policy_version=parsed.risk_policy_version,
        as_of_date=parsed.as_of_date,
    )
    return CaseWorkProductVersion(**(values | change))


def content_hash(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("with_context", [False, True])
def test_runtime_round_trip_is_tenant_scoped_and_needs_no_report_or_pdf(storage, with_context):
    engine, _, clinic, actor, _, case_id, _, foreign, _, _ = storage
    row = version(clinic, actor, case_id, context=with_context)
    identity, expected = row.id, deepcopy(row.payload_json)
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        session.add(row)
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        stored = session.get(CaseWorkProductVersion, identity)
        assert stored.payload_json == expected and stored.review_state == "UNAPPROVED"
        assert session.scalar(text("SELECT count(*) FROM case_reports WHERE case_id=:id"),
                              {"id": case_id}) == 0
    for scope in (foreign, None):
        with Session(engine) as session, session.begin():
            if scope is not None:
                tenant(session, scope)
            assert session.get(CaseWorkProductVersion, identity) is None


def test_same_version_is_globally_unique_across_authors_in_one_case(storage):
    engine, _, clinic, actor, other, case_id, *_ = storage
    product = uuid4()
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        session.add(version(clinic, actor, case_id, product=product))
        session.flush()
        with pytest.raises(IntegrityError) as caught, session.begin_nested():
            session.add(version(clinic, other, case_id, product=product))
            session.flush()
        assert caught.value.orig.diag.constraint_name == "uq_work_product_case_version"


@pytest.mark.parametrize("fault", ["foreign-case", "foreign-author", "foreign-reviewer",
                                   "foreign-tenant"])
def test_cross_clinic_links_and_writes_are_rejected(storage, fault):
    engine, _, clinic, actor, _, case_id, _, foreign, foreign_actor, foreign_case = storage
    product = uuid4()
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        session.add(version(clinic, actor, case_id, product=product))
        session.flush()
        kwargs = {}
        row_clinic, row_case = clinic, case_id
        if fault == "foreign-case":
            row_case = foreign_case
        elif fault == "foreign-author":
            kwargs["author_membership_id"] = foreign_actor
        elif fault == "foreign-reviewer":
            kwargs.update(origin="HUMAN", review_state="HUMAN_APPROVED",
                          reviewed_by_membership_id=foreign_actor, reviewed_at=datetime.now(UTC))
        else:
            row_clinic, row_case = foreign, foreign_case
        with pytest.raises(DBAPIError), session.begin_nested():
            session.add(version(row_clinic, actor, row_case, product=product, number=2, **kwargs))
            session.flush()


@pytest.mark.parametrize("fault", ["missing-parent", "null-parent", "nonconsecutive", "zero",
                                   "other-case-parent"])
def test_versions_require_the_immediate_parent_in_the_same_case_and_product(storage, fault):
    engine, _, clinic, actor, _, case_id, second_case, *_ = storage
    product = uuid4()
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        if fault != "missing-parent":
            session.add(version(clinic, actor, case_id, product=product))
            session.flush()
        row = version(clinic, actor, second_case if fault == "other-case-parent" else case_id,
                      product=product, number=2)
        if fault == "null-parent":
            row.parent_version = None
        elif fault == "nonconsecutive":
            row.version, row.parent_version = 4, 2
        elif fault == "zero":
            row.version = 0
        row.payload_json = {**row.payload_json, "version": row.version,
                            "parentVersion": row.parent_version}
        row.content_sha256 = content_hash(row.payload_json)
        with pytest.raises(IntegrityError) as caught, session.begin_nested():
            session.add(row)
            session.flush()
        expected = ("fk_work_product_parent_version" if fault in {
            "missing-parent", "other-case-parent",
        } else "ck_work_product_parent")
        assert caught.value.orig.diag.constraint_name == expected


@pytest.mark.parametrize("fault", ["hash", "payload-clinic", "payload-case", "payload-version",
                                   "payload-date", "payload-policy", "context", "oversize",
                                   "null-required-json", "fake-approval"])
def test_payload_integrity_and_binding_cannot_be_satisfied_by_a_label_or_null(storage, fault):
    engine, _, clinic, actor, _, case_id, *_ = storage
    row = version(clinic, actor, case_id, context=True)
    if fault == "hash":
        row.content_sha256 = "d" * 64
    elif fault == "context":
        row.evidence_trace_sha256 = "d" * 64
    else:
        payload = deepcopy(row.payload_json)
        if fault == "payload-clinic":
            payload["clinicId"] = str(uuid4())
        elif fault == "payload-case":
            payload["source"]["caseId"] = str(uuid4())
        elif fault == "payload-version":
            payload["version"] = 2
        elif fault == "payload-date":
            payload["asOfDate"] = "2026-10-27"
        elif fault == "payload-policy":
            payload["riskPolicyVersion"] = "dental-risk.v1"
        elif fault == "oversize":
            payload["summary"] = "x" * 70000
        elif fault == "null-required-json":
            del payload["clinicId"]
        else:
            payload["status"] = "APPROVED"
        # An attacker can recompute a hash; identity/state checks must still reject.
        row.payload_json = payload
        row.content_sha256 = content_hash(payload)
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(row)
            session.flush()


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_runtime_cannot_mutate_versions_even_after_reprovisioning(storage, operation):
    engine, _, clinic, actor, _, case_id, *_ = storage
    row = version(clinic, actor, case_id)
    identity = row.id
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        session.add(row)
    provision_runtime_role()
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        assert not session.scalar(text("SELECT has_table_privilege(current_user, "
                                        "'case_work_product_versions', :op)"), {"op": operation})
        statement = ("UPDATE case_work_product_versions SET review_state='HUMAN_APPROVED' "
                     if operation == "UPDATE" else "DELETE FROM case_work_product_versions ")
        with pytest.raises(DBAPIError), session.begin_nested():
            session.execute(text(statement + "WHERE id=:id"), {"id": identity})


def test_immutable_trigger_also_blocks_owner_update_without_explicit_ddl_override(storage):
    engine, owner, clinic, actor, _, case_id, *_ = storage
    row = version(clinic, actor, case_id)
    identity = row.id
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        session.add(row)
    with owner.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(text("UPDATE case_work_product_versions SET origin='HUMAN' "
                                "WHERE id=:id"), {"id": identity})


@pytest.mark.parametrize("fault", ["ai-approved", "missing-reviewer", "missing-time",
                                   "other-author-reviewer", "human-first"])
def test_review_metadata_is_coherent_and_never_approved_by_an_ai_origin(storage, fault):
    engine, _, clinic, actor, other, case_id, *_ = storage
    product = uuid4()
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        if fault != "human-first":
            session.add(version(clinic, actor, case_id, product=product))
            session.flush()
        row = version(clinic, actor, case_id, product=product,
                      number=1 if fault == "human-first" else 2,
                      origin="HUMAN", review_state="HUMAN_APPROVED",
                      reviewed_by_membership_id=actor, reviewed_at=datetime.now(UTC))
        if fault == "ai-approved":
            row.origin = "AI"
        elif fault == "missing-reviewer":
            row.reviewed_by_membership_id = None
        elif fault == "missing-time":
            row.reviewed_at = None
        elif fault == "other-author-reviewer":
            row.reviewed_by_membership_id = other
        with pytest.raises(IntegrityError) as caught, session.begin_nested():
            session.add(row)
            session.flush()
        assert caught.value.orig.diag.constraint_name == "ck_work_product_review_metadata"


@pytest.mark.parametrize("state", ["expired", "purged"])
def test_content_cannot_be_reintroduced_after_case_retention(storage, state):
    engine, owner, clinic, actor, _, case_id, *_ = storage
    with owner.begin() as connection:
        connection.execute(text("UPDATE cases SET retention_due_at=:due, content_purged_at=:purged "
                                "WHERE id=:id"), {
            "id": case_id, "due": datetime.now(UTC) - timedelta(seconds=1),
            "purged": datetime.now(UTC) if state == "purged" else None,
        })
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        with pytest.raises(DBAPIError), session.begin_nested():
            session.add(version(clinic, actor, case_id))
            session.flush()


def test_real_retention_erases_all_versions_but_keeps_metadata_only_audit(storage):
    engine, owner, clinic, actor, other, case_id, *_ = storage
    product, audit_id = uuid4(), uuid4()
    revised = version(clinic, other, case_id, product=product, number=2,
                      origin="HUMAN", review_state="HUMAN_REVISED")
    metadata = {"workProductId": str(product), "version": 2,
                "contentSha256": revised.content_sha256}
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        session.add(version(clinic, actor, case_id, product=product))
        session.flush()
        session.add(revised)
        session.add(AuditEvent(
            id=audit_id, clinic_id=clinic, actor_membership_id=other,
            action="WORK_PRODUCT_STORAGE_PROBE", resource_type="CASE_WORK_PRODUCT",
            resource_id=product, metadata_json=metadata, correlation_id=uuid4(),
        ))
    with owner.begin() as connection:
        connection.execute(text("UPDATE cases SET retention_due_at=:due WHERE id=:id"),
                           {"id": case_id, "due": datetime.now(UTC) - timedelta(seconds=1)})
    with Session(engine) as session, session.begin():
        assert session.scalar(text("SELECT public.purge_expired_case_content()")) >= 1
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        assert session.get(Case, case_id).status == "CONTENT_PURGED"
        assert not session.scalars(select(CaseWorkProductVersion).where(
            CaseWorkProductVersion.case_id == case_id,
        )).all()
        assert session.get(AuditEvent, audit_id).metadata_json == metadata


def test_insert_waits_for_case_lock_then_rechecks_expiration(storage):
    engine, owner, clinic, actor, _, case_id, *_ = storage
    entered = Queue()

    def insert():
        with Session(engine) as session, session.begin():
            tenant(session, clinic)
            entered.put(session.scalar(text("SELECT pg_backend_pid()")))
            session.add(version(clinic, actor, case_id))
            session.flush()

    with ThreadPoolExecutor(max_workers=1) as executor, owner.connect() as connection:
        transaction = connection.begin()
        try:
            blocker = connection.scalar(text("SELECT pg_backend_pid()"))
            connection.execute(text("SELECT id FROM cases WHERE id=:id FOR UPDATE"),
                               {"id": case_id})
            connection.execute(text("UPDATE cases SET retention_due_at=:due WHERE id=:id"),
                               {"id": case_id, "due": datetime.now(UTC) - timedelta(seconds=1)})
            future = executor.submit(insert)
            waiting = entered.get(timeout=3)
            deadline = time.monotonic() + 3
            while blocker not in connection.scalar(
                text("SELECT pg_blocking_pids(:pid)"), {"pid": waiting},
            ):
                assert not future.done(), "insert bypassed the case retention lock"
                assert time.monotonic() < deadline, "insert did not reach the expected case lock"
                time.sleep(0.01)
            transaction.commit()
            with pytest.raises(DBAPIError):
                future.result(timeout=3)
        finally:
            if transaction.is_active:
                transaction.rollback()


def test_nonempty_downgrade_refuses_to_erase_version_history(storage):
    engine, owner, clinic, actor, _, case_id, *_ = storage
    row = version(clinic, actor, case_id)
    identity = row.id
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        session.add(row)
    config = Config(str(Path(__file__).parents[3] / "alembic.ini"))
    with pytest.raises(ProgrammingError, match="cannot downgrade"):
        command.downgrade(config, "e21b7c4d8a90")
    with owner.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "f31c8d5a9e20"
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        assert session.get(CaseWorkProductVersion, identity) is not None


def test_downgrade_cannot_mistake_rls_hidden_history_for_empty_table(storage, monkeypatch):
    engine, owner, clinic, actor, _, case_id, *_ = storage
    row = version(clinic, actor, case_id)
    with Session(engine) as session, session.begin():
        tenant(session, clinic)
        session.add(row)
    path = Path(__file__).parents[1] / "migrations/versions/" \
        "f31c8d5a9e20_add_internal_work_product_versions.py"
    spec = spec_from_file_location("_aif_non_bypass_downgrade_probe", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    role = "aif_owner_" + uuid4().hex
    quoted = owner.dialect.identifier_preparer.quote(role)
    with owner.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(f"CREATE ROLE {quoted} NOSUPERUSER NOBYPASSRLS NOLOGIN")
            connection.exec_driver_sql(f"GRANT USAGE, CREATE ON SCHEMA public TO {quoted}")
            connection.exec_driver_sql(
                f"ALTER TABLE public.case_work_product_versions OWNER TO {quoted}"
            )
            for name in ("purge_expired_case_content", "guard_case_work_product_insert"):
                connection.exec_driver_sql(f"ALTER FUNCTION public.{name}() OWNER TO {quoted}")
            connection.exec_driver_sql(f"SET LOCAL ROLE {quoted}")
            connection.execute(text("SELECT set_config('app.current_clinic_id', '', true)"))
            assert not connection.scalar(text(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ))
            assert connection.scalar(text("SELECT count(*) FROM case_work_product_versions")) == 0
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            with (pytest.raises(ProgrammingError, match="cannot downgrade"),
                  connection.begin_nested()):
                migration.downgrade()
            assert connection.scalar(text("SELECT relforcerowsecurity FROM pg_class "
                                          "WHERE relname='case_work_product_versions'"))
            connection.exec_driver_sql("RESET ROLE")
            assert connection.scalar(text("SELECT count(*) FROM case_work_product_versions")) >= 1
        finally:
            # Restore ownership, function text, FORCE RLS and the temporary role
            # even if a faulty downgrade actually drops the table in the probe.
            transaction.rollback()


def test_empty_additive_migration_round_trip_restores_previous_retention(monkeypatch):
    """Use a unique sibling DB; never clear another test's retained versions to downgrade."""
    identity = "dental_wp_roundtrip_" + uuid4().hex
    owner_url = owner_database_url()
    admin = create_engine(owner_url, isolation_level="AUTOCOMMIT")
    isolated = create_engine(owner_url.set(database=identity))
    quoted = admin.dialect.identifier_preparer.quote(identity)
    config = Config(str(Path(__file__).parents[3] / "alembic.ini"))
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quoted}")
        monkeypatch.setenv("POSTGRES_DB", identity)
        command.upgrade(config, "e21b7c4d8a90")
        with isolated.connect() as connection:
            before = connection.scalar(text(
                "SELECT pg_get_functiondef('public.purge_expired_case_content()'::regprocedure)"
            ))
        command.upgrade(config, "head")
        with isolated.connect() as connection:
            assert connection.scalar(text("SELECT relrowsecurity AND relforcerowsecurity "
                                          "FROM pg_class "
                                          "WHERE relname='case_work_product_versions'"))
        command.downgrade(config, "e21b7c4d8a90")
        with isolated.connect() as connection:
            missing = connection.scalar(text(
                "SELECT to_regclass('public.case_work_product_versions')"
            ))
            assert missing is None
            assert connection.scalar(text(
                "SELECT pg_get_functiondef('public.purge_expired_case_content()'::regprocedure)"
            )) == before
        command.upgrade(config, "head")
    finally:
        isolated.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quoted}")
        admin.dispose()
