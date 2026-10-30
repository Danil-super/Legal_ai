"""Real report-free late analysis; all people, evidence and situations are fictional."""

import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.analysis_contracts import PreparationResult
from legal_core.case_retention import purge_expired_case_content
from legal_core.database import database_url, owner_database_url
from telegram_gateway.case_wizard import facts_from_v2_data
from test_case_api import actor_headers, application_client, seed_admin
from test_factual_safety_intake import screening
from test_factual_safety_postgres import guided_data
from test_legal_editor_workspace_api import _ingest, _version_payload, _write_manifest
from test_risk_policy_v4_postgres import activate_v4
from test_verified_copy_analysis_e2e import EDITOR_KEY, INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

ACTION = "Зафиксировать обращение пациента во внутреннем журнале."
pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.fixture
def prepared_case(isolated_database, tmp_path):
    actor = 79_000_000_000 + uuid4().int % 100_000_000
    clinic, _ = seed_admin(actor)
    activate_v4()
    version = _ingest(_write_manifest(
        tmp_path, fragment_text="Синтетическая норма: платные медицинские услуги. " + ACTION,
    ))
    engine = create_engine(owner_database_url())
    try:
        with application_client() as client:
            with engine.connect() as connection:
                editor = connection.scalar(text(
                    "SELECT telegram_user_id FROM users WHERE system_role='LEGAL_EDITOR' LIMIT 1"
                ))
            approved = client.post(f"/v1/legal/review-queue/{version}/approval-events",
                                   headers={**actor_headers(editor, uuid4()),
                                            "X-Legal-Editor-Gateway-Key": EDITOR_KEY},
                                   json=_version_payload(version))
            assert approved.status_code == 200, approved.text
            created = client.post("/v1/cases", headers=actor_headers(actor, uuid4()), json={
                "intakeSchemaVersion": "dental-case-intake.v2", "channel": "TELEGRAM",
            })
            assert created.status_code == 201, created.text
            case = created.json()["id"]
            data = guided_data() | {"situationAreas": ["TREATMENT"],
                                    "affectedServices": ["Синтетическая услуга"],
                                    "safetyScreening": screening()}
            added = client.post(f"/v1/cases/{case}/facts",
                                headers=actor_headers(actor, uuid4()), json={
                                    "questionId": "synthetic-preparation",
                                    "intakeSchemaVersion": "dental-case-intake.v2",
                                    "facts": facts_from_v2_data(data),
                                })
            assert added.status_code == 200, added.text
            finalized = client.post(f"/v1/cases/{case}/intake-finalizations",
                                    headers=actor_headers(actor, uuid4()), json={})
            assert finalized.status_code == 200, finalized.text
            headers = {**actor_headers(actor, uuid4()), "X-Agent-Internal-Key": INTERNAL_KEY}
            context = client.get(f"/v1/cases/{case}/analysis-context", headers=headers)
            assert context.status_code == 200, context.text
            yield client, actor, clinic, case, headers, context.json(), engine
    finally:
        engine.dispose()


def _submission(context, *, questions=True, claims=False):
    result = {
        "asOfDate": context["asOfDate"],
        "expectedFactSnapshotSha256": context["factSnapshotSha256"],
        "expectedEvidenceTraceSha256": context["evidenceTraceSha256"],
        "expectedClinicDocumentContextTraceSha256": context["clinicDocumentContextTraceSha256"],
        "expectedRiskPolicyVersion": context["riskPolicyVersion"],
        "claims": [], "semanticReviews": [],
        "clarificationRequests": [{"questionId": "RESPONSE_PURPOSE"}] if questions else [],
    }
    if claims:
        fragment = context["evidence"][0]["fragmentId"]
        result.update(claims=[{
            "claimId": "synthetic-action", "kind": "ACTION", "text": ACTION,
            "evidenceFragmentIds": [fragment], "requiredFactKeys": [],
        }], semanticReviews=[{
            "claimId": "synthetic-action", "verdict": "SUPPORTED",
            "reviewedFragmentIds": [fragment],
        }])
    return result


def _count(connection, table, case):
    assert table in {"case_reports", "case_analysis_runs", "case_risk_assessments"}
    return connection.scalar(text(f"SELECT count(*) FROM {table} WHERE case_id=:id"), {"id": case})


@pytest.mark.parametrize("partial_claim", [False, True])
def test_needs_facts_persists_real_run_and_replays_without_any_report(prepared_case, monkeypatch,
                                                                    partial_claim):
    client, _, _, case, headers, context, engine = prepared_case
    monkeypatch.setattr("legal_core.analysis_api.render_report_pdf",
                        lambda *args: pytest.fail("intermediate result must never render a PDF"))
    payload = _submission(context, claims=partial_claim)
    path = f"/v1/cases/{case}/preparation-submissions"
    first = client.post(path, headers=headers, json=payload)
    assert first.status_code == 201, first.text
    result = PreparationResult.model_validate(first.json()).root
    assert result.status == "NEEDS_FACTS"
    assert result.source.case_id == UUID(case)
    assert result.source.fact_snapshot_sha256 == context["factSnapshotSha256"]
    assert result.source.context.evidence_trace_sha256 == context["evidenceTraceSha256"]
    assert result.clarification_requests[0].question_id == "RESPONSE_PURPOSE"
    assert "report" not in first.json()
    replay = client.post(path, headers=headers, json=payload)
    assert replay.status_code == 200 and replay.json() == first.json()
    with engine.connect() as connection:
        assert _count(connection, "case_reports", case) == 0
        assert _count(connection, "case_analysis_runs", case) == 1
        assert _count(connection, "case_risk_assessments", case) == 1
        assert connection.scalar(text("SELECT id FROM case_analysis_runs WHERE case_id=:id"),
                                 {"id": case}) == result.source.operation_id
        record = connection.execute(text(
            "SELECT resource_type,resource_id FROM idempotency_records "
            "WHERE key=:key AND scope=:scope"
        ), {"key": headers["Idempotency-Key"],
            "scope": f"cases:{case}:preparation-submissions"}).one()
        assert record == ("CASE_PREPARATION", UUID(case))
        metadata = connection.scalar(text(
            "SELECT metadata_json FROM audit_events WHERE action='CASE_PREPARATION_COMPLETED' "
            "AND resource_id=:id"
        ), {"id": case})
        assert metadata["status"] == "NEEDS_FACTS"
        assert "Синтетическая услуга" not in str(metadata)


def test_empty_claims_and_questions_are_limited_not_a_fake_ready_report(prepared_case):
    client, _, _, case, headers, context, engine = prepared_case
    response = client.post(f"/v1/cases/{case}/preparation-submissions", headers=headers,
                           json=_submission(context, questions=False))
    assert response.status_code == 201, response.text
    result = PreparationResult.model_validate(response.json()).root
    assert result.status == "LIMITED"
    assert result.reason_code == "EVIDENCE_NOT_VERIFIED"
    assert "report" not in response.json()
    with engine.connect() as connection:
        assert _count(connection, "case_reports", case) == 0


def test_ready_has_a_real_source_bound_report_and_downloadable_pdf(prepared_case):
    client, _, _, case, headers, context, engine = prepared_case
    response = client.post(f"/v1/cases/{case}/preparation-submissions", headers=headers,
                           json=_submission(context, questions=False, claims=True))
    assert response.status_code == 201, response.text
    result = PreparationResult.model_validate(response.json()).root
    assert result.status == "READY"
    with engine.connect() as connection:
        assert _count(connection, "case_reports", case) == 1
    pdf = client.get(f"/v1/reports/{result.report.id}/pdf", headers=headers)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert result.report.report_json["recommendations"]["items"]
    replay = client.post(f"/v1/cases/{case}/preparation-submissions", headers=headers,
                         json=_submission(context, questions=False, claims=True))
    assert replay.status_code == 200 and replay.json() == response.json()
    with engine.connect() as connection:
        assert _count(connection, "case_reports", case) == 1


def test_wrong_context_question_tenant_and_job_version_cannot_write_results(prepared_case):
    client, actor, _, case, headers, context, engine = prepared_case
    path = f"/v1/cases/{case}/preparation-submissions"
    payload = _submission(context)
    foreign = actor + 200_000_000
    seed_admin(foreign)
    attempts = [
        (headers | {"X-Agent-Internal-Key": "x" * 32}, payload, 403),
        ({**actor_headers(foreign, uuid4()), "X-Agent-Internal-Key": INTERNAL_KEY}, payload, 404),
        (headers, payload | {"expectedFactSnapshotSha256": "f" * 64}, 409),
        (headers, payload | {"clarificationRequests": [
            {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 1},
        ]}, 422),
        (headers | {"X-Analysis-Job-Id": str(uuid4())}, payload, 409),
    ]
    for request_headers, body, expected in attempts:
        response = client.post(path, headers=request_headers, json=body)
        assert response.status_code == expected, response.text
    with engine.connect() as connection:
        assert _count(connection, "case_reports", case) == 0
        assert _count(connection, "case_analysis_runs", case) == 0


def test_preparation_content_follows_actual_case_purge_and_expired_replay_is_denied(prepared_case):
    client, _, _, case, headers, context, engine = prepared_case
    payload = _submission(context)
    path = f"/v1/cases/{case}/preparation-submissions"
    assert client.post(path, headers=headers, json=payload).status_code == 201
    with engine.begin() as connection:
        connection.execute(text("UPDATE cases SET retention_due_at=clock_timestamp() "
                                "-interval '1 second' WHERE id=:id"), {"id": case})
    assert client.post(path, headers=headers, json=payload).status_code == 410

    async def purge():
        runtime = create_async_engine(database_url())
        try:
            return await purge_expired_case_content(async_sessionmaker(runtime))
        finally:
            await runtime.dispose()
    assert asyncio.run(purge()) == 1
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM idempotency_records WHERE resource_type='CASE_PREPARATION' "
            "AND resource_id=:id"
        ), {"id": case}) == 0
    assert client.post(path, headers=headers, json=payload).status_code == 410


@pytest.mark.parametrize("tamper", ["source_case", "checksum", "resource"])
def test_tampered_preparation_cache_cannot_be_replayed_as_a_trusted_result(prepared_case, tamper):
    client, _, _, case, headers, context, engine = prepared_case
    payload = _submission(context)
    path = f"/v1/cases/{case}/preparation-submissions"
    assert client.post(path, headers=headers, json=payload).status_code == 201
    assignments = {
        "source_case": "response_json=jsonb_set(response_json,'{source,caseId}',"
        "to_jsonb(CAST(:other AS text)))",
        "checksum": "response_json=jsonb_set(response_json,'{clarificationRequests}',"
        "CAST(:questions AS jsonb))",
        "resource": "resource_id=:other_uuid",
    }
    with engine.begin() as connection:
        connection.execute(text(
            f"UPDATE idempotency_records SET {assignments[tamper]} WHERE scope=:scope AND key=:key"
        ), {"scope": f"cases:{case}:preparation-submissions", "key": headers["Idempotency-Key"],
            "other": str(uuid4()), "other_uuid": uuid4(), "questions": json.dumps([
                {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
            ])})
    response = client.post(path, headers=headers, json=payload)
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "PREPARATION_RESULT_INVALID"


def test_same_clinic_noncreator_and_lawyer_cannot_submit_or_replay_preparation(prepared_case):
    client, actor, clinic, case, headers, context, engine = prepared_case
    path = f"/v1/cases/{case}/preparation-submissions"
    payload = _submission(context)
    assert client.post(path, headers=headers, json=payload).status_code == 201
    for index, role in enumerate(["CLINIC_OWNER", "CLINIC_ADMIN", "CLINIC_LAWYER"], 1):
        other = actor + index * 200_000_000
        _, member = seed_admin(other, entitlement_status=None, role=role)
        with engine.begin() as connection:
            connection.execute(text("UPDATE clinic_users SET clinic_id=:clinic WHERE id=:id"),
                               {"clinic": clinic, "id": member})
            connection.execute(text(
                "INSERT INTO subscription_entitlements"
                "(clinic_id,user_id,status,plan_code,starts_at) "
                "SELECT :clinic,user_id,'ACTIVE','MVP',clock_timestamp()-interval '1 day' "
                "FROM clinic_users WHERE id=:id"
            ), {"clinic": clinic, "id": member})
        response = client.post(path, headers=headers | {"X-Telegram-User-Id": str(other)},
                               json=payload)
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "CASE_NOT_FOUND"


def test_same_case_receipt_cannot_replace_a_different_request_result(prepared_case):
    client, _, _, case, headers, context, engine = prepared_case
    path = f"/v1/cases/{case}/preparation-submissions"
    original = _submission(context)
    first = client.post(path, headers=headers, json=original)
    other_headers = headers | {"Idempotency-Key": str(uuid4())}
    second = client.post(path, headers=other_headers,
                         json=_submission(context, questions=False))
    assert first.status_code == second.status_code == 201
    assert first.json()["status"] == "NEEDS_FACTS"
    assert second.json()["status"] == "LIMITED"
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE idempotency_records original SET response_json=other.response_json "
            "FROM idempotency_records other WHERE original.scope=:scope "
            "AND other.scope=:scope AND original.key=:original AND other.key=:other"
        ), {"scope": f"cases:{case}:preparation-submissions",
            "original": headers["Idempotency-Key"], "other": other_headers["Idempotency-Key"]})
    replay = client.post(path, headers=headers, json=original)
    assert replay.status_code == 502, replay.text
    assert replay.json()["error"]["code"] == "PREPARATION_RESULT_INVALID"


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("same_key", [False, True])
def test_parallel_preparation_cannot_finalize_the_same_case_twice(prepared_case, monkeypatch,
                                                                version, same_key):
    import legal_core.analysis_api as api

    client, _, _, case, headers, context, engine = prepared_case
    entered, release = threading.Event(), threading.Event()
    counter_lock = threading.Lock()
    entrants = 0
    original = api._tenant_case

    async def synchronize_initial_read(*args, **kwargs):
        nonlocal entrants
        result = await original(*args, **kwargs)
        with counter_lock:
            entrants += 1
            initial = entrants <= 2
            if entrants == 2:
                entered.set()
        if initial:
            assert await asyncio.to_thread(release.wait, 10)
        return result

    monkeypatch.setattr(api, "_tenant_case", synchronize_initial_read)
    shared_key = uuid4()

    def submit():
        route = "preparation-submissions" if version == 2 else "analysis-submissions"
        return client.post(f"/v1/cases/{case}/{route}",
                           headers=headers | {"Idempotency-Key": str(shared_key if same_key
                                                                     else uuid4())},
                           json=_submission(context, questions=False, claims=True))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(submit), pool.submit(submit)
        try:
            assert entered.wait(10)
        finally:
            release.set()
        responses = first.result(15), second.result(15)
    assert sorted(response.status_code for response in responses) == (
        [200, 201] if same_key else [201, 409]
    )
    if same_key:
        assert responses[0].json() == responses[1].json()
    with engine.connect() as connection:
        assert _count(connection, "case_reports", case) == 1
        assert _count(connection, "case_analysis_runs", case) == 1
