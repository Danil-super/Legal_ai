"""Frozen-fact scope and blocked-report proofs, using synthetic inputs only."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core import analysis_clarifications
from legal_core.analysis_clarifications import ClarificationRequest
from legal_core.contracts import CaseStatus, FactKey
from legal_core.database import owner_database_url
from legal_core.reports import build_intake_report
from telegram_gateway.case_wizard import facts_from_v2_data
from test_case_api import actor_headers, application_client, seed_admin
from test_factual_safety_intake import screening
from test_factual_safety_postgres import guided_data
from test_legal_editor_workspace_api import _ingest, _version_payload, _write_manifest
from test_risk_policy_v4_postgres import activate_v4
from test_verified_copy_analysis_e2e import isolated_database  # noqa: F401


def _facts() -> dict:
    return {"INTAKE_VERSION": "GUIDED_V2", "SITUATION_AREAS": ["TREATMENT"],
            "AFFECTED_SERVICES": ["Синтетическая услуга А", "Синтетическая услуга Б"]}


def _request(question: str = "SERVICE_PAYMENT_BASIS", slot: int | None = 0):
    return ClarificationRequest(questionId=question, serviceSlot=slot)


def test_accepts_unanswered_questions_for_existing_services_and_global_purpose() -> None:
    requests = [_request(), _request("SERVICE_PROVIDED_DATE", 1),
                _request("RESPONSE_PURPOSE", None)]
    assert analysis_clarifications.validate_request_scope(requests, _facts()) == requests


@pytest.mark.parametrize("changed", [
    {"INTAKE_VERSION": "GUIDED_V1"}, {"AFFECTED_SERVICES": []},
    {"AFFECTED_SERVICES": ["only one"]}, {"SITUATION_AREAS": ["SERVICE"]},
])
def test_service_questions_never_guess_legacy_or_foreign_service_slots(changed: dict) -> None:
    with pytest.raises(ValueError):
        analysis_clarifications.validate_request_scope(
            [_request("SERVICE_PROVIDED_DATE", 1)], _facts() | changed,
        )


@pytest.mark.parametrize("value", ["PAID", "PUBLICLY_COVERED", "OTHER", "UNKNOWN"])
def test_confirmed_answers_including_unknown_are_not_requested_again(value: str) -> None:
    facts = _facts() | {"ANALYSIS_CLARIFICATION_ANSWERS": {"answers": [{
        "questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": value,
    }]}}
    with pytest.raises(ValueError):
        analysis_clarifications.validate_request_scope([_request()], facts)
    assert analysis_clarifications.validate_request_scope(
        [_request("SERVICE_PAYMENT_BASIS", 1)], facts,
    ) == [_request("SERVICE_PAYMENT_BASIS", 1)]


def test_global_purpose_does_not_require_a_treatment_service() -> None:
    facts = {"INTAKE_VERSION": "GUIDED_V2", "SITUATION_AREAS": ["SERVICE"]}
    request = _request("RESPONSE_PURPOSE", None)
    assert analysis_clarifications.validate_request_scope([request], facts) == [request]


def test_malformed_stored_answers_cannot_be_ignored_to_request_more() -> None:
    with pytest.raises(ValueError):
        analysis_clarifications.validate_request_scope(
            [_request()], _facts() | {"ANALYSIS_CLARIFICATION_ANSWERS": {"answers": "bad"}},
        )


def test_builder_retains_questions_but_no_legal_advice_or_inferred_answer() -> None:
    request = _request()
    facts = {FactKey.INTAKE_VERSION: "GUIDED_V2", FactKey.AFFECTED_SERVICES: ["Услуга"]}
    report = build_intake_report(
        report_id=uuid4(), case_id=uuid4(), public_number="SYNTHETIC-CLARIFICATION",
        case_status=CaseStatus.ANALYSIS_BLOCKED, report_version=2,
        generated_at=datetime.now(UTC), facts=facts, missing_facts=[],
        clarification_requests=[request], block_reason_code="FACTUAL_CLARIFICATION_REQUIRED",
    )
    assert report.clarification_requests == [request]
    assert report.summary.analysis_availability.status == "BLOCKED"
    assert report.recommendations.items == []
    assert report.legal_conclusions == []
    assert report.draft_response.text is None
    assert report.facts == {key.value: value for key, value in facts.items()}


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PG")
@pytest.mark.usefixtures("isolated_database")
@pytest.mark.parametrize("mode", [
    "questions_only", "verified_partial", "date_gap", "date_gap_existing", "answer_bundle_unknown",
])
def test_real_submission_persists_blocked_questions_and_idempotent_replay(
    tmp_path, mode: str,
) -> None:
    actor = 76_000_000_000 + uuid4().int % 1_000_000_000
    other = actor + 2_000_000_000
    seed_admin(actor)
    seed_admin(other)
    activate_v4()
    version = _ingest(_write_manifest(
        tmp_path, fragment_text=("Синтетическая норма: платные медицинские услуги. "
                                 "Зафиксировать обращение пациента во внутреннем журнале."),
    ))
    data = guided_data() | {
        "situationAreas": ["TREATMENT"], "affectedServices": ["Синтетическая услуга"],
        "safetyScreening": screening(),
    }
    key = "synthetic-analysis-e2e-internal-" + "x" * 32
    with application_client() as client:
        # The isolated fixture supplies keys; approve synthetic evidence only.
        engine = create_engine(owner_database_url())
        with engine.connect() as connection:
            editor = connection.scalar(text(
                "SELECT telegram_user_id FROM users WHERE system_role='LEGAL_EDITOR' LIMIT 1"
            ))
        approved = client.post(
            f"/v1/legal/review-queue/{version}/approval-events",
            headers={**actor_headers(editor, uuid4()),
                     "X-Legal-Editor-Gateway-Key": "synthetic-analysis-e2e-editor-" + "y" * 32},
            json=_version_payload(version),
        )
        assert approved.status_code == 200, approved.text
        workflow = client.post(
            f"/v1/telegram-case-workflows/{uuid4()}/submissions", headers=actor_headers(actor),
            json={"intakeSchemaVersion": "dental-case-intake.v2", "locale": "ru-RU",
                  "facts": facts_from_v2_data(data)},
        )
        assert workflow.status_code == 201, workflow.text
        case_id = workflow.json()["case"]["id"]
        internal = {**actor_headers(actor, uuid4()), "X-Agent-Internal-Key": key}
        context_response = client.get(f"/v1/cases/{case_id}/analysis-context", headers=internal)
        assert context_response.status_code == 200, context_response.text
        context = context_response.json()
        questions = [] if mode in {"date_gap", "answer_bundle_unknown"} else [
            {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0}]
        expected_questions = questions + ([{
            "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
        }] if mode.startswith("date_gap") else [])
        payload = {
            "asOfDate": context["asOfDate"],
            "expectedFactSnapshotSha256": context["factSnapshotSha256"],
            "expectedEvidenceTraceSha256": context["evidenceTraceSha256"],
            "expectedClinicDocumentContextTraceSha256": context["clinicDocumentContextTraceSha256"],
            "expectedRiskPolicyVersion": context["riskPolicyVersion"],
            "claims": [], "semanticReviews": [], "clarificationRequests": questions,
        }
        if mode != "questions_only":
            fragment = context["evidence"][0]["fragmentId"]
            payload.update(claims=[{
                "claimId": "synthetic-action", "kind": "ACTION",
                "text": "Зафиксировать обращение пациента во внутреннем журнале.",
                "evidenceFragmentIds": [fragment],
                "requiredFactKeys": ["AFFECTED_SERVICES"] if mode.startswith("date_gap") else
                ["ANALYSIS_CLARIFICATION_ANSWERS"] if mode == "answer_bundle_unknown" else [],
            }], semanticReviews=[{
                "claimId": "synthetic-action", "verdict": "SUPPORTED",
                "reviewedFragmentIds": [fragment],
            }])
        path = f"/v1/cases/{case_id}/analysis-submissions"
        rejected = client.post(path, headers=internal, json=payload | {
            "clarificationRequests": [{"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 1}],
        })
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"]["code"] == "CLARIFICATION_REQUEST_NOT_APPLICABLE"
        foreign = client.post(path, headers={**actor_headers(other, uuid4()),
                                             "X-Agent-Internal-Key": key}, json=payload)
        assert foreign.status_code == 404
        first = client.post(path, headers=internal, json=payload)
        assert first.status_code == 201, first.text
        report = first.json()["report"]["reportJson"]
        assert first.json()["analysisAllowed"] is False
        assert report["summary"]["analysisAvailability"] == {
            "status": "BLOCKED", "reasonCode": "ANALYSIS_CLARIFICATION_ANSWERS_UNKNOWN"
            if mode == "answer_bundle_unknown" else "FACTUAL_CLARIFICATION_REQUIRED",
        }
        assert report.get("clarificationRequests", []) == expected_questions
        assert report["recommendations"]["items"] == []
        assert report["legalConclusions"] == []
        assert report["factSnapshotSha256"] == context["factSnapshotSha256"]
        assert report["facts"] == context["facts"]
        replay = client.post(path, headers=internal, json=payload)
        assert replay.status_code == 200
        assert replay.json() == first.json()
        with engine.connect() as connection:
            assert connection.scalar(text(
                "SELECT count(*) FROM case_reports WHERE case_id=:case"
            ), {"case": case_id}) == 2
            metadata = connection.scalar(text(
                "SELECT metadata_json FROM audit_events WHERE action='CASE_ANALYSIS_COMPLETED' "
                "AND metadata_json->>'reportId'=:report"
            ), {"report": report["reportId"]})
            assert metadata["clarificationRequestCount"] == len(expected_questions)
            assert metadata["clarificationQuestionIds"] == [
                q["questionId"] for q in expected_questions]
            assert "answers" not in metadata
            assert "Синтетическая услуга" not in str(metadata)
        engine.dispose()
