"""Audit regression: a live lease must still authorize the final report write.

Uses existing isolated PostgreSQL approval/intake fixtures. Only report persistence
is delayed; no source, tenant, verifier or risk checks are bypassed.
"""

import asyncio
import os
from uuid import uuid4

import pytest
from legal_core import analysis_api
from sqlalchemy import text
from test_preparation_analysis import _submission
from test_preparation_analysis import prepared_case as prepared_case
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="requires disposable PostgreSQL",
)


def _expire_during_receipt_insert(engine):
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION synthetic_expire_submission() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pg_sleep(GREATEST(0, extract(epoch FROM (
                    (SELECT lease_until FROM analysis_jobs WHERE id=NEW.key::uuid)
                    -clock_timestamp()))) + 0.1);
                RETURN NEW;
            END $$
        """))
        connection.execute(text("""
            CREATE TRIGGER synthetic_receipt_wait BEFORE INSERT ON idempotency_records
            FOR EACH ROW EXECUTE FUNCTION synthetic_expire_submission()
        """))


@pytest.mark.parametrize("delay_stage", ["report", "receipt_flush", "final_source_check"])
@pytest.mark.parametrize("result_version", [1, 2])
def test_normal_submission_rechecks_lease_after_report_delay(
    prepared_case,
    monkeypatch,
    result_version,
    delay_stage,
):
    client, _, _, case, headers, context, engine = prepared_case
    job, token = uuid4(), uuid4()
    with engine.begin() as connection:
        initial_status = connection.scalar(text("SELECT status FROM cases WHERE id=:case"),
                                           {"case": case})
        connection.execute(
            text(
                "INSERT INTO analysis_jobs (id,clinic_id,case_id,actor_membership_id,"
                "message_id,result_version,state,lease_token,lease_until) "
                "SELECT :job,clinic_id,id,created_by_membership_id,456,:version,'RUNNING',"
                ":token,clock_timestamp()+interval '5 seconds' FROM cases WHERE id=:case"
            ),
            {"job": job, "version": result_version, "token": token, "case": case},
        )

    original_store = analysis_api._store_report
    observed = {}

    async def delayed_store(session, **kwargs):
        remaining = float(
            await session.scalar(
                text(
                    "SELECT extract(epoch FROM (lease_until-clock_timestamp())) "
                    "FROM analysis_jobs WHERE id=:job"
                ),
                {"job": job},
            )
        )
        assert remaining > 0, "test setup lease expired before the controlled delay"
        observed["remaining_before_delay"] = remaining
        await asyncio.sleep(remaining + 0.1)
        observed["expired_before_store"] = bool(
            await session.scalar(
                text("SELECT lease_until<=clock_timestamp() FROM analysis_jobs WHERE id=:job"),
                {"job": job},
            )
        )
        return await original_store(session, **kwargs)

    if delay_stage == "report":
        monkeypatch.setattr(analysis_api, "_store_report", delayed_store)
    elif delay_stage == "receipt_flush":
        # An actual PostgreSQL write wait: the receipt INSERT belongs to final
        # flush, after report persistence. The database wall clock advances.
        _expire_during_receipt_insert(engine)
    else:
        original_check = analysis_api.require_case_material_context_current

        async def delayed_source_check(session, **kwargs):
            remaining = float(await session.scalar(text(
                "SELECT extract(epoch FROM (lease_until-clock_timestamp())) "
                "FROM analysis_jobs WHERE id=:job"
            ), {"job": job}))
            assert remaining > 0
            await session.execute(text("SELECT pg_sleep(:seconds)"),
                                  {"seconds": remaining + 0.1})
            observed["expired_before_store"] = True
            return await original_check(session, **kwargs)

        monkeypatch.setattr(analysis_api, "require_case_material_context_current",
                            delayed_source_check)
    path = "analysis-submissions" if result_version == 1 else "preparation-submissions"
    response = client.post(
        f"/v1/cases/{case}/{path}",
        headers=headers
        | {
            "Idempotency-Key": str(job),
            "X-Analysis-Job-Id": str(job),
            "X-Analysis-Job-Token": str(token),
        },
        json=_submission(context, questions=False, claims=True),
    )
    with engine.connect() as connection:
        reports = connection.scalar(
            text("SELECT count(*) FROM case_reports WHERE case_id=:case"), {"case": case}
        )
        receipts = connection.scalar(
            text("SELECT count(*) FROM idempotency_records WHERE key=:job AND state='SUCCEEDED'"),
            {"job": str(job)},
        )
    if delay_stage != "receipt_flush":
        assert observed.get("expired_before_store") is True, observed
    assert response.status_code == 409, {
        "status": response.status_code,
        "body": response.json(),
        "reports_after_expiry": reports,
        "receipts_after_expiry": receipts,
        "observed": observed,
    }
    assert response.json()["error"]["code"] == "ANALYSIS_JOB_LEASE_EXPIRED"
    assert reports == receipts == 0
    with engine.connect() as connection:
        for table in ("case_analysis_runs", "case_risk_assessments"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE case_id=:case"),
                                     {"case": case}) == 0
        assert connection.scalar(text("SELECT state FROM analysis_jobs WHERE id=:job"),
                                 {"job": job}) == "RUNNING"
        assert connection.scalar(text("SELECT status FROM cases WHERE id=:case"),
                                 {"case": case}) == initial_status


@pytest.mark.parametrize("result_version", [1, 2])
def test_normal_submission_and_replay_with_live_lease(prepared_case, result_version):
    client, _, _, case, headers, context, engine = prepared_case
    job, token = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO analysis_jobs (id,clinic_id,case_id,actor_membership_id,"
            "message_id,result_version,state,lease_token,lease_until) "
            "SELECT :job,clinic_id,id,created_by_membership_id,456,:version,'RUNNING',"
            ":token,clock_timestamp()+interval '30 seconds' FROM cases WHERE id=:case"
        ), {"job": job, "version": result_version, "token": token, "case": case})
    path = "analysis-submissions" if result_version == 1 else "preparation-submissions"
    headers = headers | {"Idempotency-Key": str(job), "X-Analysis-Job-Id": str(job),
                         "X-Analysis-Job-Token": str(token)}
    payload = _submission(context, questions=False, claims=True)
    first = client.post(f"/v1/cases/{case}/{path}", headers=headers, json=payload)
    replay = client.post(f"/v1/cases/{case}/{path}", headers=headers, json=payload)
    assert first.status_code == 201, first.text
    assert replay.status_code == 200 and replay.json() == first.json()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM case_reports WHERE case_id=:case"),
                                 {"case": case}) == 1


@pytest.mark.parametrize("result_kind", ["report_free", "precontext"])
def test_report_free_and_precontext_results_cannot_commit_after_receipt_wait(
    prepared_case, result_kind,
):
    client, _, _, case, headers, context, engine = prepared_case
    job, token = uuid4(), uuid4()
    with engine.begin() as connection:
        initial_status = connection.scalar(text("SELECT status FROM cases WHERE id=:case"),
                                           {"case": case})
        if result_kind == "precontext":
            # A real unavailable production corpus gives a terminal precontext result.
            connection.execute(text("UPDATE users SET system_role=NULL "
                                    "WHERE system_role='LEGAL_EDITOR'"))
        connection.execute(text(
            "INSERT INTO analysis_jobs (id,clinic_id,case_id,actor_membership_id,"
            "message_id,result_version,state,lease_token,lease_until) "
            "SELECT :job,clinic_id,id,created_by_membership_id,456,2,'RUNNING',"
            ":token,clock_timestamp()+interval '3 seconds' FROM cases WHERE id=:case"
        ), {"job": job, "token": token, "case": case})
    _expire_during_receipt_insert(engine)
    headers = headers | {"Idempotency-Key": str(job), "X-Analysis-Job-Id": str(job),
                         "X-Analysis-Job-Token": str(token)}
    path = "preparation-context" if result_kind == "precontext" else "preparation-submissions"
    response = client.post(
        f"/v1/cases/{case}/{path}", headers=headers,
        json={} if result_kind == "precontext" else _submission(context, questions=False),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ANALYSIS_JOB_LEASE_EXPIRED"
    with engine.connect() as connection:
        for table in ("case_reports", "case_analysis_runs", "case_risk_assessments"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE case_id=:case"),
                                     {"case": case}) == 0
        assert connection.scalar(text("SELECT count(*) FROM idempotency_records "
                                      "WHERE key=:job AND state='SUCCEEDED'"),
                                 {"job": str(job)}) == 0
        assert connection.scalar(text("SELECT status FROM cases WHERE id=:case"),
                                 {"case": case}) == initial_status
