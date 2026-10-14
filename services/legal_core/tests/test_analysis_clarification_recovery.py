"""Report-bound recovery preserves immutable fictional history and attempted answers."""

import asyncio
import json
import os
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.api_contracts import CorrectionSource, TelegramIntakeDraftUpdateRequest
from legal_core.case_clarification import ClarificationDraftRequest, _prefill
from legal_core.case_api import _canonical_hash, resolve_actor
from legal_core.contracts import CanonicalReport, FactKey
from legal_core.database import database_url, owner_database_url
from legal_core.models import Case, CaseReport
from legal_core.analysis_api import _store_report
from legal_core.risk_policy_approval import RiskPolicyApproval, approve_risk_policy
from test_case_api import actor_headers, application_client, seed_admin
from test_clarification_recovery import _confirm, _guided_data, _source_rows
from test_legal_editor_workspace_api import _seed_user
import test_verified_copy_analysis_e2e

isolated_database = test_verified_copy_analysis_e2e.isolated_database

requires_pg = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)
REQUESTS = [{"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0}]


@pytest.fixture
def protected_database(isolated_database):
    editor = 92_000_000_000 + uuid4().int % 100_000_000
    _seed_user(editor, system_role="LEGAL_EDITOR")

    async def approve():
        engine = create_async_engine(database_url())
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            for version in (1, 3):
                await approve_risk_policy(sessions, RiskPolicyApproval(
                    reviewer_telegram_user_id=editor, version=version,
                    high_demand_threshold_kopecks=5_000_000,
                    incident_triggers_reviewed=True, monetary_threshold_reviewed=True,
                    escalation_rules_reviewed=True, early_triage_enabled=version == 3,
                    supersede_approved=version == 3,
                    guided_v2_explicit_signals_enabled=version == 3,
                    direct_v1_supersession_reviewed=version == 3,
                    factual_safety_intake_enabled=version == 3,
                    factual_safety_intake_reviewed=version == 3,
                ))
        finally:
            await engine.dispose()

    asyncio.run(approve())


def _append_report(actor: int, workflow: dict, requests=None, corruption=None) -> dict:
    """Use the real Core canonical report writer; corrupt only a new synthetic row."""
    async def append():
        engine = create_async_engine(database_url())
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                context = await resolve_actor(session, actor)
                case = await session.scalar(select(Case).where(
                    Case.id == UUID(workflow["case"]["id"])
                ).with_for_update())
                previous = await session.scalar(select(CaseReport).where(
                    CaseReport.case_id == case.id
                ).order_by(CaseReport.report_version.desc()).limit(1))
                data = deepcopy(previous.report_json)
                data.update(reportId=str(uuid4()), reportVersion=previous.report_version + 1)
                data["clarificationRequests"] = REQUESTS if requests is None else requests
                canonical = CanonicalReport.model_validate(data)
                result = await _store_report(session, actor=context, case=case, canonical=canonical)
                await session.commit()
                return result.model_dump(mode="json", by_alias=True)
        finally:
            await engine.dispose()

    result = asyncio.run(append())
    if corruption:
        # Persist malformed test metadata in a new row, never mutate the historical source.
        engine = create_engine(owner_database_url())
        try:
            with engine.begin() as connection:
                row = connection.execute(text(
                    "SELECT to_jsonb(r) FROM case_reports r WHERE id=:id"
                ), {"id": result["id"]}).scalar_one()
                row["id"] = str(uuid4())
                row["report_version"] += 1
                row["report_json"]["reportId"] = row["id"]
                row["report_json"]["reportVersion"] = row["report_version"]
                if corruption == "facts":
                    row["facts_snapshot_sha256"] = "0" * 64
                elif corruption == "content":
                    row["content_sha256"] = "0" * 64
                elif corruption == "scope":
                    row["report_json"]["clarificationRequests"][0]["serviceSlot"] = 4
                else:
                    row["report_json"]["case"]["id"] = str(uuid4())
                if corruption != "content":
                    row["content_sha256"] = _canonical_hash(row["report_json"])
                connection.execute(text(
                    "INSERT INTO case_reports SELECT * FROM jsonb_populate_record("
                    "NULL::case_reports, CAST(:row AS jsonb))"
                ), {"row": json.dumps(row)})
                result["id"] = row["id"]
        finally:
            engine.dispose()
    return result


def _preview(client, actor: int, case_id: str):
    return client.get(f"/v1/cases/{case_id}/clarification-drafts/preview",
                      headers=actor_headers(actor))


def _recover(client, actor: int, case_id: str, binding: dict, key=None):
    return client.post(f"/v1/cases/{case_id}/clarification-drafts",
                       headers=actor_headers(actor, key or uuid4()),
                       json={k: v for k, v in binding.items() if k != "caseId"})


@pytest.mark.parametrize("changes", [
    {"expectedReportId": str(uuid4())}, {"expectedReportSha256": "a" * 64},
    {"expectedReportId": "bad", "expectedReportSha256": "a" * 64},
    {"expectedReportId": str(uuid4()), "expectedReportSha256": "a" * 63},
])
def test_recovery_request_rejects_unpaired_or_malformed_report_binding(changes):
    with pytest.raises(ValidationError):
        ClarificationDraftRequest.model_validate({"expectedFactSnapshotSha256": "a" * 64,
                                                  **changes})


def test_report_binding_serializes_only_when_provided():
    original = {"expectedFactSnapshotSha256": "a" * 64}
    assert ClarificationDraftRequest.model_validate(original).model_dump(
        mode="json", by_alias=True
    ) == original
    bound = {**original, "expectedReportId": str(uuid4()), "expectedReportSha256": "b" * 64}
    assert ClarificationDraftRequest.model_validate(bound).model_dump(
        mode="json", by_alias=True
    ) == bound


@pytest.mark.parametrize("changes", [
    {"reportId": str(uuid4())}, {"reportSha256": "a" * 64},
    {"reportId": "bad", "reportSha256": "a" * 64},
])
def test_correction_source_requires_paired_valid_report_fields(changes):
    with pytest.raises(ValidationError):
        CorrectionSource.model_validate({"caseId": str(uuid4()),
                                         "factSnapshotSha256": "a" * 64, **changes})


@pytest.mark.parametrize("requests", [
    REQUESTS * 2,
    [{"questionId": "RESPONSE_PURPOSE"}] + REQUESTS * 3,
    [{"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": True}],
    [{"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "question": "injection"}],
    {"requests": REQUESTS},
])
def test_recovery_draft_requests_keep_the_strict_bounded_list_contract(requests):
    with pytest.raises(ValidationError):
        TelegramIntakeDraftUpdateRequest.model_validate({
            "expectedRevision": 1, "wizardState": "ANALYSIS_CLARIFICATION",
            "draftData": {"intakeVersion": 2, "analysisClarificationRequests": requests},
        })


def test_prefill_keeps_confirmed_and_unknown_attempted_answers():
    attempted = {"answers": [
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "UNKNOWN"},
        {"questionId": "RESPONSE_PURPOSE", "value": "ANSWER_REQUEST"},
    ]}
    facts = {FactKey.ANALYSIS_CLARIFICATION_ANSWERS: attempted}
    _, data = _prefill(facts, uuid4(), "a" * 64)
    assert data["analysisClarificationAnswers"] == attempted
    assert facts[FactKey.ANALYSIS_CLARIFICATION_ANSWERS] == attempted


@requires_pg
def test_bound_recovery_uses_latest_core_requests_and_replays_without_history_changes(
    protected_database,
):
    actor = 94_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    data = _guided_data()
    data["analysisClarificationAnswers"] = {"answers": [
        {"questionId": "RESPONSE_PURPOSE", "value": "UNKNOWN"}
    ]}
    with application_client() as client:
        workflow = _confirm(client, actor, data)
        report = _append_report(actor, workflow)
        case_id = workflow["case"]["id"]
        before = _source_rows(case_id)
        preview = _preview(client, actor, case_id)
        assert preview.status_code == 200, preview.text
        binding = preview.json()
        assert binding == {"caseId": case_id,
                           "expectedFactSnapshotSha256": report["reportJson"]["factSnapshotSha256"],
                           "expectedReportId": report["id"],
                           "expectedReportSha256": _canonical_hash(report["reportJson"])}
        key = uuid4()
        response = _recover(client, actor, case_id, binding, key)
        assert response.status_code == 201, response.text
        draft = response.json()
        assert draft["wizardState"] == "ANALYSIS_CLARIFICATION"
        assert draft["draftData"]["analysisClarificationRequests"] == REQUESTS
        assert draft["draftData"]["analysisClarificationAnswers"] == (
            report["reportJson"]["facts"]["ANALYSIS_CLARIFICATION_ANSWERS"]
        )
        assert draft["draftData"]["correctionSource"] == {
            "caseId": case_id, "factSnapshotSha256": binding["expectedFactSnapshotSha256"],
            "reportId": report["id"], "reportSha256": binding["expectedReportSha256"],
        }
        replay = _recover(client, actor, case_id, binding, key)
        assert replay.status_code == 200 and replay.json() == draft
        assert client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                          headers=actor_headers(actor)).json() == draft
        assert _source_rows(case_id) == before
        engine = create_engine(owner_database_url())
        try:
            with engine.connect() as connection:
                metadata = connection.execute(text(
                    "SELECT metadata_json FROM audit_events WHERE resource_id=:id "
                    "AND action='CASE_CLARIFICATION_DRAFT_CREATED'"
                ), {"id": draft["id"]}).scalar_one()
                assert metadata == {
                    "sourceCaseId": case_id,
                    "sourceFactSha256": binding["expectedFactSnapshotSha256"],
                    "sourceReportId": report["id"],
                    "sourceReportSha256": binding["expectedReportSha256"],
                    "clarificationRequestCount": 1,
                }
        finally:
            engine.dispose()


@requires_pg
@pytest.mark.parametrize("stale", ["id", "hash", "latest", "unbound"])
def test_pending_report_requires_current_binding(protected_database, stale):
    actor = 95_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    with application_client() as client:
        workflow = _confirm(client, actor, _guided_data())
        _append_report(actor, workflow)
        case_id = workflow["case"]["id"]
        binding = _preview(client, actor, case_id).json()
        if stale == "id":
            binding["expectedReportId"] = str(uuid4())
        elif stale == "hash":
            binding["expectedReportSha256"] = "0" * 64
        elif stale == "latest":
            _append_report(actor, workflow)
        else:
            binding.pop("expectedReportId", None)
            binding.pop("expectedReportSha256", None)
        before = _source_rows(case_id)
        rejected = _recover(client, actor, case_id, binding)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == (
            "CLARIFICATION_REPORT_BINDING_REQUIRED" if stale == "unbound" else "CASE_REPORT_STALE"
        )
        assert _source_rows(case_id) == before
        assert client.get("/v1/telegram-intake-drafts",
                          headers=actor_headers(actor)).json()["items"] == []


@requires_pg
@pytest.mark.parametrize("corruption", ["facts", "content", "scope", "case"])
def test_invalid_retained_source_report_fails_closed(protected_database, corruption):
    actor = 96_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    with application_client() as client:
        workflow = _confirm(client, actor, _guided_data())
        _append_report(actor, workflow, corruption=corruption)
        case_id = workflow["case"]["id"]
        before = _source_rows(case_id)
        rejected = _preview(client, actor, case_id)
        assert rejected.status_code == 409, rejected.text
        assert _source_rows(case_id) == before


@requires_pg
def test_unknown_attempt_is_retained_without_reopening_the_question(protected_database):
    actor = 97_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    data = _guided_data()
    data["analysisClarificationAnswers"] = {"answers": [
        {**REQUESTS[0], "value": "UNKNOWN"}
    ]}
    with application_client() as client:
        workflow = _confirm(client, actor, data)
        case_id = workflow["case"]["id"]
        binding = _preview(client, actor, case_id).json()
        result = _recover(client, actor, case_id, binding)
        assert result.status_code == 201, result.text
        assert result.json()["wizardState"] == "SAFETY"
        assert result.json()["draftData"]["analysisClarificationAnswers"] == (
            data["analysisClarificationAnswers"]
        )
        assert not result.json()["draftData"].get("analysisClarificationRequests")
