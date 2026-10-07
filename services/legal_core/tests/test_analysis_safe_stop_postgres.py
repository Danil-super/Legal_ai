"""Real tenant/runtime proof that pausing automation leaves lawyer and draft work usable."""

import os
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.database import database_url, owner_database_url
from legal_core.case_api import _canonical_hash
from legal_core.models import IdempotencyRecord
from test_analysis_api import _report_response, _submission
from test_analysis_jobs import seed_confirmed_case
from test_case_api import actor_headers, application_client, seed_admin
from test_risk_policy_v3_postgres import prepare_policy, reset_policies
from telegram_gateway.case_wizard import facts_from_v2_data

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL",
)


def test_stop_replays_a_committed_result_without_generating_a_new_report(monkeypatch):
    user = 8_730_000_000 + uuid4().int % 1_000_000_000
    case_id = seed_confirmed_case(user)
    idempotency_key = uuid4()
    payload = _submission().model_dump(mode="json", by_alias=True)
    report = _report_response().model_dump(mode="json", by_alias=True)
    report["caseId"] = case_id
    result = {
        "analysisAllowed": True, "riskLevel": "LOW", "escalationRequired": False,
        "report": report,
    }

    async def store():
        engine = create_async_engine(database_url())
        try:
            async with async_sessionmaker(engine)() as session:
                # Actor context establishes the same server-selected tenant as a request.
                from legal_core.case_api import resolve_actor
                actor = await resolve_actor(session, user)
                session.add(IdempotencyRecord(
                    clinic_id=actor.clinic_id, actor_membership_id=actor.membership_id,
                    scope=f"cases:{case_id}:analysis-submissions", key=str(idempotency_key),
                    request_sha256=_canonical_hash(payload), state="SUCCEEDED",
                    response_json=result,
                ))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(store())
    key = "synthetic-safe-stop-internal-key-1234567890"
    monkeypatch.setenv("AGENT_INTERNAL_KEY", key)
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    with application_client() as client:
        response = client.post(f"/v1/cases/{case_id}/analysis-submissions", json=payload,
                               headers={**actor_headers(user, idempotency_key),
                                        "X-Agent-Internal-Key": key})
        assert response.status_code == 200, response.json()
        assert response.json()["report"] == report
        assert response.json()["riskLevel"] == "LOW"


def test_stop_fences_all_analysis_boundaries_without_writing_or_consuming_jobs(monkeypatch):
    user = 8_740_000_000 + uuid4().int % 1_000_000_000
    other = user + 1
    case_id = seed_confirmed_case(user)
    seed_admin(other)
    key = "synthetic-safe-stop-internal-key-1234567890"
    monkeypatch.setenv("AGENT_INTERNAL_KEY", key)
    monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://127.0.0.1:1")
    with application_client() as client:
        queued = client.post(
            f"/v1/cases/{case_id}/analysis-jobs", headers=actor_headers(user),
            json={"messageId": 33},
        )
        assert queued.status_code == 202, queued.json()
        job_id = queued.json()["jobId"]
        monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
        internal = {**actor_headers(user, uuid4()), "X-Agent-Internal-Key": key}
        requests = [
            client.post(f"/v1/cases/{case_id}/analysis-jobs",
                        headers=actor_headers(user), json={"messageId": 34}),
            client.get(f"/v1/cases/{case_id}/analysis-context", headers=internal),
            client.post(f"/v1/cases/{case_id}/analysis-submissions", headers=internal, json={
                "asOfDate": "2026-09-01", "expectedFactSnapshotSha256": "a" * 64,
                "expectedEvidenceTraceSha256": "b" * 64,
                "expectedClinicDocumentContextTraceSha256": "c" * 64,
                "expectedRiskPolicyVersion": "dental-risk.v3", "claims": [],
                "semanticReviews": [],
            }),
        ]
        for response in requests:
            assert response.status_code == 503, response.json()
            assert response.json()["error"]["code"] == "ANALYSIS_SAFE_STOP"
        state = client.get(f"/v1/analysis-jobs/{job_id}", headers=actor_headers(user))
        assert state.status_code == 200
        assert state.json()["state"] == "QUEUED"
        assert client.post(
            f"/v1/cases/{case_id}/analysis-jobs", headers=actor_headers(other),
            json={"messageId": 35},
        ).status_code == 404
        assert client.get(
            f"/v1/cases/{case_id}/analysis-context", headers=actor_headers(user),
        ).status_code == 403

    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as conn:
            assert conn.scalar(text(
                "SELECT count(*) FROM analysis_jobs WHERE case_id=:case",
            ), {"case": case_id}) == 1
            assert conn.scalar(text(
                "SELECT count(*) FROM case_reports WHERE case_id=:case",
            ), {"case": case_id}) == 0
    finally:
        engine.dispose()


def test_v3_stop_preserves_confirmation_drafts_and_full_lawyer_workflow(monkeypatch):
    reset_policies()
    try:
        prepare_policy()
        monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
        owner = 8_850_000_000 + uuid4().int % 1_000_000_000
        lawyer, other = owner + 1, owner + 2
        seed_admin(owner, role="CLINIC_OWNER")
        seed_admin(other, role="CLINIC_LAWYER")
        facts = facts_from_v2_data({
            "intakeVersion": 2, "incomingKind": "COMPLAINT",
            "incomingSourceStatus": "NOT_ATTACHED", "situationAreas": ["SERVICE"],
            "eventSummary": "Вымышленная ситуация для проверки работы юриста.",
            "eventDate": {"date": "2026-09-01", "precision": "EXACT"},
            "conflictStage": "FIRST", "clinicActions": ["NOTHING_YET"],
            "healthSignals": ["HOSPITALIZATION"], "caseMaterialsStatus": "NOT_ATTACHED",
        })
        with application_client() as client:
            added = client.post("/v1/clinic/members", headers=actor_headers(owner), json={
                "telegramUserId": lawyer, "role": "CLINIC_LAWYER",
            })
            assert added.status_code == 201, added.json()
            draft = client.post("/v1/telegram-intake-drafts",
                                headers=actor_headers(owner, uuid4()), json={})
            assert draft.status_code == 201
            draft_id = draft.json()["id"]
            saved = client.put(f"/v1/telegram-intake-drafts/{draft_id}",
                               headers=actor_headers(owner, uuid4()), json={
                "expectedRevision": 1, "wizardState": "SERVICE_TYPE",
                "draftData": {"incident_type": "QUALITY_COMPLAINT"},
            })
            assert saved.status_code == 200, saved.json()
            confirmed = client.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                                    headers=actor_headers(owner), json={
                "intakeSchemaVersion": "dental-case-intake.v2", "locale": "ru-RU",
                "facts": facts,
            })
            assert confirmed.status_code == 201, confirmed.json()
            escalation = confirmed.json()["case"]["earlyEscalationId"]
            assert escalation is not None
            path = f"/v1/case-escalations/{escalation}"
            assert client.get(path, headers=actor_headers(other)).status_code == 404
            queue = client.get("/v1/case-escalations", headers=actor_headers(lawyer))
            matching = [item for item in queue.json()["items"]
                        if item["escalationId"] == escalation]
            assert len(matching) == 1 and matching[0]["riskLevel"] == "CRITICAL"
            assert client.post(path + "/claim", headers=actor_headers(lawyer)).status_code == 200
            posted = client.post(path + "/discussion", headers=actor_headers(lawyer, uuid4()),
                                 json={"body": "Вымышленное уточнение от тестового юриста."})
            assert posted.status_code == 201, posted.json()
            discussion = client.get(path + "/discussion", headers=actor_headers(lawyer))
            assert discussion.status_code == 200
            resolved = client.post(path + "/resolve", headers=actor_headers(lawyer),
                                  json={"body": "Тестовое обращение проверено вручную."})
            assert resolved.status_code == 200, resolved.json()
            assert resolved.json()["status"] == "RESOLVED"
            preserved = client.get(f"/v1/telegram-intake-drafts/{draft_id}",
                                   headers=actor_headers(owner))
            assert preserved.status_code == 200
            assert preserved.json()["revision"] == 2
            assert preserved.json()["draftData"] == saved.json()["draftData"]
    finally:
        reset_policies()
