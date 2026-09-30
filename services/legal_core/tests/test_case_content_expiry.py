"""Retention deadlines cover live content and successful idempotency replays alike."""

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.case_api import _canonical_hash
from legal_core.database import owner_database_url
from test_analysis_api import _submission
from test_case_api import actor_headers, application_client, seed_admin, workflow_submission

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


def test_expired_case_blocks_cached_analysis_pdf_and_workflow_without_breaking_valid_replay(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "synthetic-internal-key-" + "x" * 32)
    actor_id = 81_000_000_000 + uuid4().int % 1_000_000_000
    clinic_id, membership_id = seed_admin(actor_id)
    workflow_id, submission_key = uuid4(), uuid4()
    workflow_path = f"/v1/telegram-case-workflows/{workflow_id}"
    workflow_payload = workflow_submission()
    payload = _submission().model_dump(mode="json", by_alias=True)
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    try:
        with application_client() as client:
            workflow = client.post(
                workflow_path + "/submissions",
                headers=actor_headers(actor_id),
                json=workflow_payload,
            )
            assert workflow.status_code == 201, workflow.json()
            case_id = workflow.json()["case"]["id"]
            report = workflow.json()["report"]
            analysis_path = f"/v1/cases/{case_id}/analysis-submissions"
            cached = {
                "analysisAllowed": True,
                "riskLevel": "LOW",
                "escalationRequired": False,
                "escalationId": None,
                "report": report,
                "clinicDocumentReadiness": [],
            }
            # Seed a committed synthetic outcome, not an LLM mock: this regression is about
            # the actual PostgreSQL idempotency response path after successful completion.
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE cases SET status='REPORT_READY' WHERE id=:id"), {"id": case_id}
                )
                connection.execute(
                    text(
                        "INSERT INTO idempotency_records (clinic_id,actor_membership_id,scope,key,"
                        "request_sha256,state,response_json) VALUES "
                        "(:clinic,:member,:scope,:key,:sha,'SUCCEEDED',CAST(:response AS jsonb))"
                    ),
                    dict(
                        clinic=clinic_id,
                        member=membership_id,
                        scope=f"cases:{case_id}:analysis-submissions",
                        key=str(submission_key),
                        sha=_canonical_hash(payload),
                        response=json.dumps(cached),
                    ),
                )
            headers = {
                **actor_headers(actor_id, submission_key),
                "X-Agent-Internal-Key": "synthetic-internal-key-" + "x" * 32,
            }
            valid = client.post(analysis_path, headers=headers, json=payload)
            assert valid.status_code == 200, valid.json()
            assert valid.json() == cached
            paths = [f"/v1/cases/{case_id}", f"/v1/reports/{report['id']}/pdf", workflow_path]
            for path in paths:
                assert client.get(path, headers=actor_headers(actor_id)).status_code == 200
            assert (
                client.post(
                    workflow_path + "/submissions",
                    headers=actor_headers(actor_id),
                    json=workflow_payload,
                ).status_code
                == 200
            )

            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE cases SET retention_due_at=now()-interval '1 second' WHERE id=:id"
                    ),
                    {"id": case_id},
                )
            expired = client.post(analysis_path, headers=headers, json=payload)
            assert expired.status_code == 410, expired.json()
            assert expired.json()["error"]["code"] == "CASE_CONTENT_EXPIRED"
            for path in paths:
                assert client.get(path, headers=actor_headers(actor_id)).status_code == 410
            assert (
                client.post(
                    workflow_path + "/submissions",
                    headers=actor_headers(actor_id),
                    json=workflow_payload,
                ).status_code
                == 410
            )

            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE cases SET content_purged_at=now() WHERE id=:id"), {"id": case_id}
                )
            purged = client.post(analysis_path, headers=headers, json=payload)
            assert purged.status_code == 410
            assert purged.json()["error"]["code"] == "CASE_CONTENT_PURGED"
    finally:
        engine.dispose()
