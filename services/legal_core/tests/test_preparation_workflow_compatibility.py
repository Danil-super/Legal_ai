"""Reader-first compatibility: a preparation receipt is not an empty report."""

import os
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from legal_core.api_contracts import (
    TelegramPreparationReceipt,
    TelegramWorkflowReceipt,
    TelegramWorkflowSubmissionRequest,
)
from legal_core.case_api import _canonical_hash
from legal_core.database import owner_database_url
from legal_core.models import Case, TelegramCaseWorkflow
from test_case_api import actor_headers, application_client, seed_admin, workflow_submission


def receipt_wire():
    return {
        "receiptVersion": 2, "workflowId": str(uuid4()), "state": "SUCCEEDED",
        "case": {
            "id": str(uuid4()), "publicNumber": "SYNTHETIC-READER",
            "status": "COLLECTING", "intakeSchemaVersion": "dental-case-intake.v2",
            "createdAt": "2026-10-01T00:00:00Z", "earlyEscalationId": None,
        },
    }


def test_shared_wire_types_preserve_the_existing_api_imports_without_duplicate_schemas():
    from legal_core import api_contracts, contracts
    for name in (
        "CaseResponse", "ReportResponse", "LegalFragmentResponse", "TelegramWorkflowResponse",
        "TelegramPreparationReceipt", "TelegramWorkflowReceipt",
    ):
        assert getattr(api_contracts, name) is getattr(contracts, name)


def test_explicit_preparation_receipt_has_no_report_or_pdf_identity():
    wire = receipt_wire()
    parsed = TypeAdapter(TelegramWorkflowReceipt).validate_python(wire)
    assert isinstance(parsed, TelegramPreparationReceipt)
    assert parsed.model_dump(mode="json", by_alias=True) == wire
    assert "report" not in type(parsed).model_fields


@pytest.mark.parametrize("version", [None, 0, 1, 3, True, "2", 2.0])
def test_report_free_receipt_requires_an_exact_supported_integer_version(version):
    wire = receipt_wire()
    if version is None:
        wire.pop("receiptVersion")
    else:
        wire["receiptVersion"] = version
    with pytest.raises(ValidationError):
        TypeAdapter(TelegramWorkflowReceipt).validate_python(wire)


@pytest.mark.parametrize("report", [None, {}, {"id": str(uuid4())}])
def test_preparation_receipt_rejects_even_a_null_or_placeholder_report(report):
    wire = receipt_wire()
    wire["report"] = report
    with pytest.raises(ValidationError):
        TypeAdapter(TelegramWorkflowReceipt).validate_python(wire)


@pytest.fixture
def stored_receipt():
    if os.getenv("POSTGRES_INTEGRATION") != "1":
        pytest.skip("requires disposable PostgreSQL")
    actor = 18_000_000_000 + uuid4().int % 100_000_000
    clinic, membership = seed_admin(actor)
    engine = create_engine(owner_database_url())
    try:
        with Session(engine) as session, session.begin():
            case = Case(clinic_id=clinic, created_by_membership_id=membership,
                        intake_schema_version="dental-case-intake.v2")
            session.add(case)
            session.flush()
            workflow = uuid4()
            normalized = TelegramWorkflowSubmissionRequest.model_validate(
                workflow_submission(),
            ).model_dump(mode="json", by_alias=True)
            session.add(TelegramCaseWorkflow(
                id=workflow, clinic_id=clinic, actor_membership_id=membership,
                case_id=case.id, request_sha256=_canonical_hash(normalized), state="SUCCEEDED",
                receipt_version=2, report_id=None,
            ))
            case_id = case.id
        yield actor, clinic, membership, workflow, case_id, engine
    finally:
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE cases SET retention_due_at=clock_timestamp()+interval '90 days' "
                "WHERE id=:id"
            ), {"id": case_id})
        engine.dispose()


def test_actual_api_reads_report_free_receipt_without_creating_report(stored_receipt):
    actor, _, _, workflow, case, engine = stored_receipt
    with application_client() as client:
        response = client.get(f"/v1/telegram-case-workflows/{workflow}",
                              headers=actor_headers(actor))
    assert response.status_code == 200, response.text
    assert response.json()["receiptVersion"] == 2
    assert response.json()["case"]["id"] == str(case)
    assert "report" not in response.json()
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM case_reports WHERE case_id=:id"
        ), {"id": case}) == 0


@pytest.mark.parametrize("denial", ["foreign_clinic", "other_creator", "revoked", "expired"])
def test_preparation_receipt_preserves_ownership_access_and_retention(stored_receipt, denial):
    actor, clinic, _, workflow, case, engine = stored_receipt
    expected = 404
    if denial == "foreign_clinic":
        actor += 100_000_000
        seed_admin(actor)
    elif denial == "other_creator":
        actor += 200_000_000
        _, membership = seed_admin(actor, entitlement_status=None)
        with engine.begin() as connection:
            connection.execute(text("UPDATE clinic_users SET clinic_id=:clinic WHERE id=:id"),
                               {"clinic": clinic, "id": membership})
            connection.execute(text(
                "INSERT INTO subscription_entitlements "
                "(clinic_id,user_id,status,plan_code,starts_at) "
                "SELECT :clinic,user_id,'ACTIVE','MVP',clock_timestamp()-interval '1 day' "
                "FROM clinic_users WHERE id=:id"
            ), {"clinic": clinic, "id": membership})
    else:
        statement = (
            "UPDATE clinic_users SET status='REVOKED' WHERE clinic_id=:id"
            if denial == "revoked" else
            "UPDATE cases SET retention_due_at=clock_timestamp()-interval '1 second' WHERE id=:id"
        )
        expected = 403 if denial == "revoked" else 410
        with engine.begin() as connection:
            connection.execute(text(statement), {"id": clinic if denial == "revoked" else case})
    with application_client() as client:
        response = client.get(f"/v1/telegram-case-workflows/{workflow}",
                              headers=actor_headers(actor))
    assert response.status_code == expected, response.text
    assert str(case) not in response.text
    assert "report" not in response.json()


def test_existing_producer_still_returns_and_replays_a_legacy_report(stored_receipt):
    actor, _, _, _, _, _ = stored_receipt
    workflow = uuid4()
    with application_client() as client:
        response = client.post(f"/v1/telegram-case-workflows/{workflow}/submissions",
                               headers=actor_headers(actor), json=workflow_submission())
        replay = client.get(f"/v1/telegram-case-workflows/{workflow}",
                            headers=actor_headers(actor))
    assert response.status_code == 201, response.text
    assert response.json() == replay.json()
    assert "receiptVersion" not in response.json()
    assert response.json()["report"]["caseId"] == response.json()["case"]["id"]
    assert response.json()["report"]["pdfSha256"]


@pytest.mark.parametrize("mode", ["replay", "expired", "changed_payload"])
def test_submission_replay_reads_v2_without_producing_another_case_or_report(stored_receipt, mode):
    actor, clinic, _, workflow, case, engine = stored_receipt
    payload = workflow_submission()
    with engine.begin() as connection:
        if mode == "expired":
            connection.execute(text(
                "UPDATE cases SET retention_due_at=clock_timestamp()-interval '1 second' "
                "WHERE id=:id"
            ), {"id": case})
        before = connection.execute(text(
            "SELECT (SELECT count(*) FROM cases WHERE clinic_id=:clinic),"
            "(SELECT count(*) FROM case_reports WHERE clinic_id=:clinic)"
        ), {"clinic": clinic}).one()
    if mode == "changed_payload":
        payload["facts"] = [fact for fact in payload["facts"] if fact["factKey"] != "SERVICE_TYPE"]
    with application_client() as client:
        response = client.post(f"/v1/telegram-case-workflows/{workflow}/submissions",
                               headers=actor_headers(actor), json=payload)
    expected = {"replay": 200, "expired": 410, "changed_payload": 422}[mode]
    assert response.status_code == expected, response.text
    if mode == "replay":
        assert response.json()["receiptVersion"] == 2
        assert response.json()["case"]["id"] == str(case)
        assert "report" not in response.json()
    else:
        assert response.json()["error"]["code"] == (
            "CASE_CONTENT_EXPIRED" if mode == "expired" else "WORKFLOW_PAYLOAD_MISMATCH"
        )
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT (SELECT count(*) FROM cases WHERE clinic_id=:clinic),"
            "(SELECT count(*) FROM case_reports WHERE clinic_id=:clinic)"
        ), {"clinic": clinic}).one() == before
