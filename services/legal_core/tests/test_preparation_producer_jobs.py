"""Actual producer-to-queue integration; fictional cases and evidence only."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_preparation_analysis import _submission
from test_preparation_analysis import isolated_database as isolated_database
from test_preparation_analysis import prepared_case as prepared_case

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("tamper", [False, True])
def test_actual_producer_receipt_is_verified_by_the_job_reader(prepared_case, tamper):
    client, _, _, case, headers, context, engine = prepared_case
    job, token = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO analysis_jobs (id,clinic_id,case_id,actor_membership_id,message_id,"
            "state,attempts,lease_token,lease_until,result_version) "
            "SELECT :job,clinic_id,id,created_by_membership_id,123,'RUNNING',1,:token,"
            "clock_timestamp()+interval '3 minutes',2 FROM cases WHERE id=:case"
        ), {"job": job, "token": token, "case": case})
    submitted = client.post(f"/v1/cases/{case}/preparation-submissions", headers=headers | {
        "Idempotency-Key": str(job), "X-Analysis-Job-Id": str(job),
        "X-Analysis-Job-Token": str(token),
    }, json=_submission(context))
    assert submitted.status_code == 201, submitted.text
    if tamper:
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE idempotency_records SET response_json=jsonb_set(response_json,"
                "'{source,operationId}',to_jsonb(CAST(:other AS text))) "
                "WHERE scope=:scope AND key=:key"
            ), {"other": str(uuid4()), "scope": f"cases:{case}:preparation-submissions",
                "key": str(job)})
    read = client.get(f"/v1/analysis-jobs/{job}", headers=headers)
    assert read.status_code == (502 if tamper else 200), read.text
    if not tamper:
        assert read.json()["result"] == submitted.json()
