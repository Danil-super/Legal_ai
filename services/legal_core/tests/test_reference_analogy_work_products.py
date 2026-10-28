"""Immutable trace persistence and authorized review without historical-workspace exposure."""

import asyncio
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.case_api import resolve_actor
from legal_core.case_preparation_contracts import InternalWorkProduct
from legal_core.database import database_url
from legal_core.work_product_storage import CaseWorkProductVersion
from test_case_api import actor_headers
from test_lawyer_draft_context import routed_case as routed_case
from test_lawyer_work_product_api import plan_path, post, submission
from test_reference_analogy_context import SCENARIO, seed_reference
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


def test_granted_producer_trace_survives_ungranted_lawyer_approval_and_withdrawal(routed_case,
                                                                             monkeypatch):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    reference = seed_reference(routed_case)
    body = submission(routed_case)
    trace = body["expectedSource"]["context"]["referenceExampleTraceSha256"]
    result = post(routed_case, body)
    assert result.status_code == 201, result.text
    with routed_case[9].connect() as connection:
        assert connection.scalar(text("SELECT reference_example_trace_sha256 "
                                      "FROM case_work_product_versions")) == trace
    client = routed_case[0]
    lawyer = next(iter(routed_case[10]))
    path = plan_path(routed_case)
    assert client.post(path.rsplit("/", 1)[0] + "/claim",
                       headers=actor_headers(lawyer)).status_code == 200
    assert client.get(f"/v1/reference-evaluations/{reference}",
                      headers=actor_headers(lawyer)).status_code == 403
    view = client.get(path, headers=actor_headers(lawyer))
    assert view.status_code == 200 and view.json()["isCurrent"], view.text
    assert SCENARIO not in view.text and "referenceExamples" not in view.text
    decision = {"expectedVersionId": view.json()["versionId"],
                "expectedContentSha256": view.json()["contentSha256"], "decision": "APPROVE"}
    approved = client.post(path + "/reviews", headers=actor_headers(lawyer, uuid4()), json=decision)
    assert approved.status_code == 201 and approved.json()["reviewState"] == "HUMAN_APPROVED"
    with routed_case[9].begin() as connection:
        traces = connection.scalars(text("SELECT reference_example_trace_sha256 "
                                         "FROM case_work_product_versions ORDER BY version")).all()
        assert traces == [trace, trace]
        connection.execute(text("DELETE FROM reference_evaluation_access_grants "
                                "WHERE membership_id=:id"), {"id": routed_case[3]})
    stale = client.get(path, headers=actor_headers(lawyer))
    assert stale.status_code == 200 and not stale.json()["isCurrent"], stale.text


def test_runtime_sql_cannot_diverge_trace_column_from_exact_immutable_source(routed_case,
                                                                         monkeypatch):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    seed_reference(routed_case)
    result = post(routed_case)
    assert result.status_code == 201, result.text

    async def insert_mismatch():
        engine = create_async_engine(database_url())
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await resolve_actor(session, routed_case[1])
                existing = await session.get(CaseWorkProductVersion,
                                             UUID(result.json()["versionId"]))
                values = {column.name: getattr(existing, column.name)
                          for column in CaseWorkProductVersion.__table__.columns}
                payload = deepcopy(values["payload_json"])
                values.update(id=uuid4(), work_product_id=uuid4(),
                              reference_example_trace_sha256="f" * 64)
                payload["workProductId"] = str(values["work_product_id"])
                values["payload_json"] = payload
                content = InternalWorkProduct.model_validate(payload)
                values["content_sha256"] = content.content_sha256
                session.add(CaseWorkProductVersion(**values))
                with pytest.raises(IntegrityError) as caught:
                    await session.commit()
                assert "ck_work_product_reference_trace" in str(caught.value.orig)
        finally:
            await engine.dispose()
    asyncio.run(insert_mismatch())


def test_nonbypass_owner_downgrade_cannot_hide_retained_reference_history(routed_case,
                                                                      monkeypatch):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    seed_reference(routed_case)
    assert post(routed_case).status_code == 201
    path = Path(__file__).parents[1] / "migrations/versions/" \
        "f42d9e6b0a31_bind_work_product_reference_trace.py"
    spec = spec_from_file_location("_reference_trace_downgrade_probe", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    owner = routed_case[9]
    role = "reference_owner_" + uuid4().hex
    quoted = owner.dialect.identifier_preparer.quote(role)
    with owner.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(f"CREATE ROLE {quoted} NOSUPERUSER NOBYPASSRLS NOLOGIN")
            connection.exec_driver_sql(f"GRANT USAGE, CREATE ON SCHEMA public TO {quoted}")
            connection.exec_driver_sql(
                f"ALTER TABLE public.case_work_product_versions OWNER TO {quoted}")
            connection.exec_driver_sql(f"SET LOCAL ROLE {quoted}")
            connection.execute(text("SELECT set_config('app.current_clinic_id', '', true)"))
            assert not connection.scalar(text(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"))
            assert connection.scalar(text("SELECT count(*) FROM case_work_product_versions")) == 0
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            with (pytest.raises(ProgrammingError, match="cannot downgrade"),
                  connection.begin_nested()):
                migration.downgrade()
            assert connection.scalar(text("SELECT relforcerowsecurity FROM pg_class "
                                          "WHERE relname='case_work_product_versions'"))
            connection.exec_driver_sql("RESET ROLE")
            assert connection.scalar(text("SELECT count(*) FROM case_work_product_versions "
                                          "WHERE reference_example_trace_sha256 IS NOT NULL")) == 1
        finally:
            transaction.rollback()
