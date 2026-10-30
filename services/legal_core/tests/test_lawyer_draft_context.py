"""Private read-only context for an already routed case, with a real running V2 lease."""

import asyncio
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.analysis_jobs import claim_job
from legal_core.database import database_url, owner_database_url
from legal_core.lawyer_draft_contracts import LawyerDraftContextRequest, LawyerDraftContextResponse
from test_case_api import actor_headers, application_client, seed_admin
from test_factual_safety_intake import screening
from test_legal_editor_workspace_api import _ingest, _version_payload, _write_manifest
from test_report_free_workflow import _payload
from test_risk_policy_v4_postgres import activate_v4, seed_lawyers
from test_verified_copy_analysis_e2e import EDITOR_KEY, INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
EVIDENCE = "Синтетическая норма: платные медицинские услуги. Зафиксировать обращение."


def run(operation):
    async def invoke():
        engine = create_async_engine(database_url())
        try:
            return await operation(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()
    return asyncio.run(invoke())


@pytest.fixture
def routed_case(isolated_database, tmp_path, monkeypatch, request):
    options = getattr(request, "param", {})
    monkeypatch.setenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "true")
    actor = 103_000_000_000 + uuid4().int % 100_000_000
    clinic, member = seed_admin(actor)
    activate_v4()
    lawyers = seed_lawyers(clinic, actor)
    version = _ingest(_write_manifest(tmp_path, fragment_text=EVIDENCE))
    owner = create_engine(owner_database_url())
    try:
        with application_client() as client:
            if options.get("approved", True):
                with owner.connect() as connection:
                    editor = connection.scalar(text("SELECT telegram_user_id FROM users "
                                                    "WHERE system_role='LEGAL_EDITOR' LIMIT 1"))
                approved = client.post(f"/v1/legal/review-queue/{version}/approval-events",
                                       headers={**actor_headers(editor, uuid4()),
                                                "X-Legal-Editor-Gateway-Key": EDITOR_KEY},
                                       json=_version_payload(version))
                assert approved.status_code == 200, approved.text
            critical = options.get("critical", False)
            data = _payload(
                eventDate=options.get("eventDate", {"date": "2026-09-12", "precision": "EXACT"}),
                healthSignals=["HOSPITALIZATION"] if critical else ["NO_KNOWN_INFORMATION"],
                safetyScreening=screening(hospitalizationReported="YES") if critical else screening(
                    moneyRequested="YES", amount={"amountKopecks": 1_000_000, "currency": "RUB"},
                ),
            )
            created = client.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                                  headers=actor_headers(actor), json=data)
            assert created.status_code == 201, created.text
            case = created.json()["case"]["id"]
            queued = client.post(f"/v1/cases/{case}/analysis-jobs", headers=actor_headers(actor),
                                 json={"messageId": 45})
            assert queued.status_code == 202, queued.text
            job = run(claim_job)
            assert job is not None and str(job.id) == queued.json()["jobId"]
            headers = {**actor_headers(actor, job.id), "X-Agent-Internal-Key": INTERNAL_KEY,
                       "X-Analysis-Job-Id": str(job.id),
                       "X-Analysis-Job-Token": str(job.lease_token)}
            early = client.post(f"/v1/cases/{case}/preparation-context", headers=headers)
            assert early.status_code == 201, early.text
            assert early.json()["status"] == "HUMAN_REVIEW"
            body = {
                "expectedPreparationOperationId": early.json()["source"]["operationId"],
                "expectedFactSnapshotSha256": early.json()["source"]["factSnapshotSha256"],
                "expectedRiskPolicyVersion": early.json()["riskPolicyVersion"],
            }
            with owner.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM escalation_notifications "
                                              "WHERE escalation_id=:id"),
                                         {"id": early.json()["escalationId"]}) == len(lawyers)
            yield (client, actor, clinic, member, case, job, headers, body, early.json(),
                   owner, lawyers)
    finally:
        owner.dispose()


def read(fixture, *, headers=None, body=None):
    client, _, _, _, case, _, bound_headers, bound_body, *_ = fixture
    return client.post(f"/v1/cases/{case}/lawyer-draft-context",
                       headers=bound_headers if headers is None else headers,
                       json=bound_body if body is None else body)


@pytest.mark.parametrize("routed_case", [{}, {"critical": True}], indirect=True)
def test_running_creator_gets_only_bound_approved_context_and_does_not_change_routing(routed_case):
    client, actor, _, _, case, job, headers, body, early, owner, _lawyers = routed_case
    tables = ("case_reports", "case_analysis_runs", "case_risk_assessments", "case_escalations",
              "escalation_notifications", "idempotency_records")
    with owner.connect() as connection:
        before = {table: connection.scalar(text(f"SELECT count(*) FROM {table}"))
                  for table in tables}
    response = read(routed_case)
    assert response.status_code == 200, response.text
    parsed = LawyerDraftContextResponse.model_validate(response.json())
    assert parsed.source.operation_id == UUID(body["expectedPreparationOperationId"])
    assert parsed.source.case_id == UUID(case) == parsed.context.case_id
    assert parsed.source.fact_snapshot_sha256 == body["expectedFactSnapshotSha256"]
    assert parsed.source.context.evidence_trace_sha256 == parsed.context.evidence_trace_sha256
    assert parsed.context.risk_policy_version == "dental-risk.v4"
    assert str(parsed.escalation_id) == early["escalationId"]
    assert parsed.risk_level == early["riskLevel"]
    assert [item.fragment_text for item in parsed.context.evidence] == [EVIDENCE]
    assert "analysisAllowed" not in response.text and "reportId" not in response.text
    assert headers["X-Analysis-Job-Token"] not in response.text
    assert "referenceCases" not in response.text and "caseMaterials" not in response.text
    # The canonical boundary remains closed even though this separate context can be read.
    assert client.get(f"/v1/cases/{case}/analysis-context", headers=headers).status_code == 409
    with owner.connect() as connection:
        assert before == {table: connection.scalar(text(f"SELECT count(*) FROM {table}"))
                          for table in tables}
        assert connection.scalar(text("SELECT state FROM analysis_jobs WHERE id=:id"),
                                 {"id": job.id}) == "RUNNING"
        assert connection.scalar(text("SELECT status FROM cases WHERE id=:id"),
                                 {"id": case}) == "ESCALATION_REQUIRED"
        audit = connection.scalar(text("SELECT metadata_json FROM audit_events "
                                       "WHERE resource_id=:id "
                                       "AND action='LAWYER_DRAFT_CONTEXT_READ'"),
                                  {"id": case})
        assert audit["preparationOperationId"] == body["expectedPreparationOperationId"]
        assert audit["factSnapshotSha256"] == body["expectedFactSnapshotSha256"]
        assert EVIDENCE not in json.dumps(audit, ensure_ascii=False)
        assert str(job.lease_token) not in str(audit)
    assert client.get(f"/v1/case-escalations/{early['escalationId']}",
                      headers=actor_headers(actor)).status_code == 200


@pytest.mark.parametrize(("patch", "code"), [
    ({"X-Agent-Internal-Key": "wrong-" + "x" * 40}, 403),
    ({"X-Agent-Internal-Key": None}, 403),
    ({"X-Analysis-Job-Token": str(uuid4())}, 409),
    ({"X-Analysis-Job-Token": None}, 409),
    ({"X-Analysis-Job-Id": None}, 409),
    ({"Idempotency-Key": str(uuid4())}, 409),
])
def test_private_key_and_the_actual_paired_job_lease_are_required(routed_case, patch, code):
    headers = {key: value for key, value in (routed_case[6] | patch).items() if value is not None}
    denied = read(routed_case, headers=headers)
    assert denied.status_code == code, denied.text
    assert "context" not in denied.json()


@pytest.mark.parametrize("fault", ["lease-expired", "job-finished", "wrong-version",
                                   "revoked", "subscription-expired", "retention", "case-closed"])
def test_current_worker_actor_and_case_fences_are_rechecked(routed_case, fault):
    _, _, clinic, member, case, job, *_ = routed_case
    owner = routed_case[9]
    statements = {
        "lease-expired": ("UPDATE analysis_jobs SET lease_until=clock_timestamp()"
                          "-interval '1 second' WHERE id=:job"),
        "job-finished": ("UPDATE analysis_jobs SET state='SUCCEEDED',lease_token=NULL,"
                         "lease_until=NULL WHERE id=:job"),
        "wrong-version": "UPDATE analysis_jobs SET result_version=1 WHERE id=:job",
        "revoked": "UPDATE clinic_users SET status='REVOKED' WHERE id=:member",
        "subscription-expired": ("UPDATE subscription_entitlements "
                                  "SET starts_at=clock_timestamp()-interval '2 days',"
                                  "ends_at=clock_timestamp()-interval '1 second' "
                                  "WHERE clinic_id=:clinic"),
        "retention": ("UPDATE cases SET retention_due_at=clock_timestamp()"
                      "-interval '1 second' WHERE id=:case"),
        "case-closed": "UPDATE cases SET status='CLOSED' WHERE id=:case",
    }
    with owner.begin() as connection:
        connection.execute(text(statements[fault]), {"job": job.id, "member": member,
                                                     "clinic": clinic, "case": case})
    denied = read(routed_case)
    assert denied.status_code == (403 if fault in {"revoked", "subscription-expired"}
                                  else 410 if fault == "retention" else 409), denied.text


@pytest.mark.parametrize("role", ["other-clinic", "CLINIC_ADMIN", "CLINIC_OWNER", "CLINIC_LAWYER"])
def test_no_other_actor_or_lawyer_can_use_the_creators_background_context(routed_case, role):
    _, actor, clinic, *_ = routed_case
    other = actor + 9_000_000_000
    _, member = seed_admin(other, role="CLINIC_ADMIN" if role == "other-clinic" else role,
                           entitlement_status="ACTIVE" if role == "other-clinic" else None)
    if role != "other-clinic":
        with routed_case[9].begin() as connection:
            connection.execute(text("UPDATE clinic_users SET clinic_id=:clinic WHERE id=:id"),
                               {"clinic": clinic, "id": member})
            connection.execute(text("INSERT INTO subscription_entitlements "
                                    "(clinic_id,user_id,plan_code,starts_at) "
                                    "SELECT clinic_id,user_id,"
                                    "'SYNTHETIC',clock_timestamp()-interval '1 day' "
                                    "FROM clinic_users WHERE id=:id"), {"id": member})
    denied = read(routed_case, headers=routed_case[6] | {"X-Telegram-User-Id": str(other)})
    assert denied.status_code == 404, denied.text
    assert denied.json()["error"]["code"] == "CASE_NOT_FOUND"


@pytest.mark.parametrize("field,value", [
    ("expectedPreparationOperationId", str(uuid4())),
    ("expectedFactSnapshotSha256", "f" * 64),
    ("expectedRiskPolicyVersion", "dental-risk.v1"),
])
def test_caller_cannot_rebind_a_retained_human_review(routed_case, field, value):
    denied = read(routed_case, body=routed_case[7] | {field: value})
    assert denied.status_code == 409, denied.text


@pytest.mark.parametrize("routed_case,code", [
    ({"eventDate": {"date": None, "precision": "UNKNOWN"}}, "ANALYSIS_DATE_UNCERTAIN"),
    ({"eventDate": {"date": "2026-09-12", "precision": "APPROXIMATE"}}, "ANALYSIS_DATE_UNCERTAIN"),
    ({"approved": False}, "LEGAL_EVIDENCE_UNAVAILABLE"),
    ({"eventDate": {"date": "2025-01-01", "precision": "EXACT"}}, "LEGAL_EVIDENCE_UNAVAILABLE"),
], indirect=["routed_case"])
def test_unknown_date_or_unapproved_inapplicable_sources_are_rejected(routed_case, code):
    denied = read(routed_case)
    assert denied.status_code == (422 if code == "ANALYSIS_DATE_UNCERTAIN" else 409), denied.text
    assert denied.json()["error"]["code"] == code
    with routed_case[9].connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM audit_events "
                                      "WHERE action='LAWYER_DRAFT_CONTEXT_READ'")) == 0
        assert connection.scalar(text("SELECT count(*) FROM case_escalations")) == 1


def test_resolved_escalation_stops_the_context_without_destroying_its_receipt(routed_case):
    client, _, _, _, _, _, _, _, early, owner, lawyers = routed_case
    lawyer = next(iter(lawyers))
    path = f"/v1/case-escalations/{early['escalationId']}"
    assert client.post(path + "/claim", headers=actor_headers(lawyer)).status_code == 200
    resolved = client.post(path + "/resolve", headers=actor_headers(lawyer),
                           json={"body": "Синтетическое внутреннее обсуждение завершено."})
    assert resolved.status_code == 200, resolved.text
    assert read(routed_case).status_code == 409
    with owner.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM idempotency_records "
                                      "WHERE resource_type='CASE_PREPARATION'")) == 1


def test_operator_feature_defaults_off_and_safe_stop_blocks_context(routed_case, monkeypatch):
    monkeypatch.delenv("AI_FIRST_LAWYER_DRAFT_ENABLED")
    assert read(routed_case).status_code == 503
    monkeypatch.setenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    assert read(routed_case).status_code == 503


def test_metadata_contract_rejects_raw_facts_permissions_and_arbitrary_sources(routed_case):
    for field in ("clinicId", "actorId", "facts", "files", "analysisAllowed", "approveNorms"):
        denied = read(routed_case, body=routed_case[7] | {field: "SYNTHETIC_PRIVATE_MARKER"})
        assert denied.status_code == 422
        assert "SYNTHETIC_PRIVATE_MARKER" not in denied.text
    assert LawyerDraftContextRequest.model_validate(routed_case[7])
    response = read(routed_case)
    assert response.status_code == 200, response.text
    wire = response.json()
    wire["source"]["context"]["evidenceTraceSha256"] = "f" * 64
    with pytest.raises(ValidationError):
        LawyerDraftContextResponse.model_validate(wire)


def test_changed_current_fact_snapshot_is_not_replaced_by_the_retained_request(routed_case):
    with routed_case[9].begin() as connection:
        connection.execute(text(
            "INSERT INTO case_facts(clinic_id,case_id,fact_key,revision,value_type,value_json,"
            "source_type,evidence_status,recorded_by_membership_id) "
            "SELECT clinic_id,case_id,fact_key,revision+1,value_type,"
            "jsonb_build_object('text',CAST(:summary AS text)),source_type,evidence_status,"
            "recorded_by_membership_id FROM case_facts "
            "WHERE case_id=:case AND fact_key='EVENT_SUMMARY' ORDER BY revision DESC LIMIT 1"
        ), {"case": routed_case[4], "summary": "Дополнительная синтетическая информация."})
    denied = read(routed_case)
    assert denied.status_code == 409, denied.text
    assert denied.json()["error"]["code"] == "LAWYER_DRAFT_CONTEXT_STALE"


def test_retiring_the_current_policy_closes_context_without_changing_escalation(routed_case):
    with routed_case[9].begin() as connection:
        connection.execute(text(
            "INSERT INTO risk_policy_events(risk_policy_id,actor_user_id,decision,"
            "expected_content_sha256,reason_code) SELECT id,approved_by_user_id,'RETIRED',"
            "content_sha256,'SYNTHETIC_POLICY_REVIEW' FROM risk_policy_versions "
            "WHERE status='APPROVED'"
        ))
        connection.execute(text("UPDATE risk_policy_versions SET status='RETIRED' "
                                "WHERE status='APPROVED'"))
    denied = read(routed_case)
    assert denied.status_code == 409, denied.text
    assert denied.json()["error"]["code"] == "RISK_POLICY_NOT_READY"
    with routed_case[9].connect() as connection:
        assert connection.scalar(text("SELECT status FROM cases WHERE id=:id"),
                                 {"id": routed_case[4]}) == "ESCALATION_REQUIRED"


@pytest.mark.parametrize("fault", ["source-operation", "source-case", "resource", "absent"])
def test_actual_retained_receipt_must_match_its_audited_source(routed_case, fault):
    assignments = {
        "source-operation": "response_json=jsonb_set(response_json,'{source,operationId}',"
                            "to_jsonb(CAST(:other AS text)))",
        "source-case": "response_json=jsonb_set(response_json,'{source,caseId}',"
                       "to_jsonb(CAST(:other AS text)))",
        "resource": "resource_id=CAST(:other AS uuid)",
        "absent": "response_json=NULL",
    }
    with routed_case[9].begin() as connection:
        connection.execute(text(
            f"UPDATE idempotency_records SET {assignments[fault]} WHERE scope=:scope AND key=:key"
        ), {"scope": f"cases:{routed_case[4]}:preparation-submissions",
            "key": str(routed_case[5].id), "other": str(uuid4())})
    denied = read(routed_case)
    assert denied.status_code == (409 if fault == "absent" else 502), denied.text
    with routed_case[9].connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM audit_events "
                                      "WHERE action='LAWYER_DRAFT_CONTEXT_READ'")) == 0


@pytest.mark.parametrize("boundary,withdrawal", [
    ("case", "revoked"), ("case", "creator"), ("case", "retention"),
    ("job", "lease-expired"), ("job", "subscription-expired"),
])
def test_context_rechecks_actual_database_lock_wait(routed_case, boundary, withdrawal):
    _, _, clinic, member, case, job, *_ = routed_case
    owner = routed_case[9]
    with owner.connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = blocker.begin()
        blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
        table = "cases" if boundary == "case" else "analysis_jobs"
        blocker.execute(text(f"SELECT id FROM {table} WHERE id=:id FOR UPDATE"),
                        {"id": case if boundary == "case" else job.id})
        future = pool.submit(read, routed_case)
        try:
            deadline = time.monotonic() + 5
            blocked = False
            while time.monotonic() < deadline:
                with owner.connect() as observer:
                    blocked = observer.scalar(text(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                        "WHERE :blocker=ANY(pg_blocking_pids(pid)))"
                    ), {"blocker": blocker_pid})
                if blocked:
                    break
                time.sleep(0.01)
            assert blocked, "the HTTP request must actually wait on its database row"
            statements = {
                "revoked": "UPDATE clinic_users SET status='REVOKED' WHERE id=:member",
                "creator": "UPDATE cases SET created_by_membership_id=(SELECT id FROM "
                           "clinic_users WHERE clinic_id=:clinic AND id<>:member "
                           "AND status='ACTIVE' LIMIT 1) WHERE id=:case",
                "retention": "UPDATE cases SET retention_due_at=clock_timestamp()"
                             "-interval '1 second' WHERE id=:case",
                "lease-expired": "UPDATE analysis_jobs SET lease_until=clock_timestamp()"
                                 "-interval '1 second' WHERE id=:job",
                "subscription-expired": "UPDATE subscription_entitlements SET "
                                        "starts_at=clock_timestamp()-interval '2 days',"
                                        "ends_at=clock_timestamp()-interval '1 second' "
                                        "WHERE clinic_id=:clinic",
            }
            blocker.execute(text(statements[withdrawal]),
                            {"clinic": clinic, "member": member, "case": case, "job": job.id})
            transaction.commit()
        finally:
            if transaction.is_active:
                transaction.rollback()
        denied = future.result(15)
    expected = {"revoked": 403, "creator": 404, "retention": 410,
                "lease-expired": 409, "subscription-expired": 403}[withdrawal]
    assert denied.status_code == expected, denied.text
    with owner.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM audit_events "
                                      "WHERE action='LAWYER_DRAFT_CONTEXT_READ'")) == 0


@pytest.mark.parametrize("withdrawal", ["membership", "policy"])
def test_access_and_policy_are_checked_again_after_actual_retrieval(
    routed_case, monkeypatch, withdrawal,
):
    import legal_core.lawyer_draft_context as context_api

    entered, release = threading.Event(), threading.Event()
    original = context_api._retrieve_analysis_state

    async def pause_after_retrieval(*args, **kwargs):
        state = await original(*args, **kwargs)
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        return state

    monkeypatch.setattr(context_api, "_retrieve_analysis_state", pause_after_retrieval)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read, routed_case)
        try:
            assert entered.wait(10), "actual retrieval must finish before access is withdrawn"
            with routed_case[9].begin() as connection:
                if withdrawal == "membership":
                    connection.execute(text("UPDATE clinic_users SET status='REVOKED' "
                                            "WHERE id=:id"),
                                       {"id": routed_case[3]})
                else:
                    connection.execute(text(
                        "INSERT INTO risk_policy_events(risk_policy_id,actor_user_id,decision,"
                        "expected_content_sha256,reason_code) "
                        "SELECT id,approved_by_user_id,'RETIRED',content_sha256,"
                        "'SYNTHETIC_POLICY_REVIEW' FROM risk_policy_versions "
                        "WHERE status='APPROVED'"
                    ))
                    connection.execute(text("UPDATE risk_policy_versions SET status='RETIRED' "
                                            "WHERE status='APPROVED'"))
        finally:
            release.set()
        denied = future.result(15)
    assert denied.status_code == (403 if withdrawal == "membership" else 409), denied.text
    with routed_case[9].connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM audit_events "
                                      "WHERE action='LAWYER_DRAFT_CONTEXT_READ'")) == 0
