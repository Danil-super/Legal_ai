"""Opt-in confirmed preparation receipts preserve facts without an intake PDF."""

import os
from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from legal_core.api_contracts import TelegramWorkflowSubmissionRequest
from legal_core.database import owner_database_url
from telegram_gateway.case_wizard import facts_from_v2_data
from test_case_api import actor_headers, application_client, seed_admin, workflow_submission
from test_case_materials_api import FakeCaseMaterialStore, _new_draft, _upload_headers
from test_factual_safety_intake import screening
from test_factual_safety_postgres import guided_data
from test_risk_policy_v4_postgres import activate_v4
from test_verified_copy_analysis_e2e import INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database


def _payload(**changes):
    data = guided_data() | {"safetyScreening": screening()} | changes
    return {"receiptVersion": 2, "intakeSchemaVersion": "dental-case-intake.v2",
            "locale": "ru-RU", "facts": facts_from_v2_data(data)}


def test_opt_in_receipt_normalization_keeps_legacy_canonical_request_unchanged():
    legacy = workflow_submission()
    assert TelegramWorkflowSubmissionRequest.model_validate(legacy).model_dump(
        mode="json", by_alias=True,
    ) == legacy
    assert TelegramWorkflowSubmissionRequest.model_validate(_payload()).model_dump(
        mode="json", by_alias=True,
    )["receiptVersion"] == 2


@pytest.mark.parametrize("version", [None, 1, 3, True, "2", 2.0])
def test_new_request_version_is_exact_not_coerced(version):
    with pytest.raises(ValidationError):
        TelegramWorkflowSubmissionRequest.model_validate(_payload() | {"receiptVersion": version})


def test_report_free_request_cannot_be_used_with_the_legacy_fact_schema():
    with pytest.raises(ValidationError):
        TelegramWorkflowSubmissionRequest.model_validate(
            workflow_submission() | {"receiptVersion": 2},
        )


@pytest.fixture
def preparation_workflow(isolated_database):
    actor = 87_000_000_000 + uuid4().int % 100_000_000
    clinic, membership = seed_admin(actor)
    activate_v4()
    engine = create_engine(owner_database_url())
    try:
        with application_client() as core:
            yield core, actor, clinic, membership, engine
    finally:
        engine.dispose()


pg = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="isolated PostgreSQL")


@pg
@pytest.mark.parametrize("event", [
    {"date": "2026-09-12", "precision": "EXACT"},
    {"date": None, "precision": "UNKNOWN"},
    {"date": "2026-09-01", "precision": "APPROXIMATE"},
])
def test_preparation_receipt_persists_attempts_and_replays_without_rendering(
    preparation_workflow, event, monkeypatch,
):
    core, actor, _, _, engine = preparation_workflow
    for target in ("build_intake_report", "render_report_pdf"):
        monkeypatch.setattr(f"legal_core.case_api.{target}",
                            lambda *args, **kwargs: pytest.fail("no preliminary report"))
    workflow = uuid4()
    path = f"/v1/telegram-case-workflows/{workflow}/submissions"
    payload = _payload(eventDate=event)
    headers = actor_headers(actor)
    created = core.post(path, headers=headers, json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["receiptVersion"] == 2 and "report" not in body
    case = body["case"]["id"]
    replay = core.post(path, headers=headers, json=payload)
    assert replay.status_code == 200 and replay.json() == body
    assert core.get(f"/v1/telegram-case-workflows/{workflow}", headers=headers).json() == body
    legacy = deepcopy(payload)
    legacy.pop("receiptVersion")
    mismatch = core.post(path, headers=headers, json=legacy)
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "WORKFLOW_PAYLOAD_MISMATCH"
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT receipt_version,report_id,case_id FROM telegram_case_workflows WHERE id=:id"
        ), {"id": workflow}).one()
        assert row.receipt_version == 2 and row.report_id is None and str(row.case_id) == case
        assert connection.scalar(text("SELECT count(*) FROM case_reports")) == 0
        assert connection.scalar(text("SELECT count(*) FROM cases")) == 1
        assert connection.scalar(text(
            "SELECT value_json FROM case_facts WHERE case_id=:id AND fact_key='EVENT_DATE'"
        ), {"id": case}) == event
        assert connection.scalar(text(
            "SELECT count(*) FROM audit_events WHERE action='CASE_REPORT_CREATED'"
        )) == 0
        audit = connection.scalar(text(
            "SELECT metadata_json FROM audit_events WHERE action='TELEGRAM_WORKFLOW_SUCCEEDED'"
        ))
        assert audit == {"caseId": case, "receiptVersion": 2, "schema": "dental-case-intake.v2"}
    if event["precision"] != "EXACT":
        limited = core.post(f"/v1/cases/{case}/preparation-context", headers={
            **actor_headers(actor, uuid4()), "X-Agent-Internal-Key": INTERNAL_KEY,
        })
        assert limited.status_code == 201, limited.text
        assert limited.json()["status"] == "LIMITED"
        assert limited.json()["reasonCode"] == "ANALYSIS_DATE_UNCERTAIN"
        assert "report" not in limited.json()


@pg
def test_absent_date_is_not_an_attempt_and_other_missing_facts_still_refuse(preparation_workflow):
    core, actor, _, _, engine = preparation_workflow
    payload = _payload(eventDate={"date": None, "precision": "UNKNOWN"})
    for missing in ("EVENT_DATE", "EVENT_SUMMARY"):
        partial = payload | {"facts": [fact for fact in payload["facts"]
                                      if fact["factKey"] != missing]}
        denied = core.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                           headers=actor_headers(actor), json=partial)
        assert denied.status_code == 422, denied.text
        assert denied.json()["error"]["code"] == "INSUFFICIENT_FACTS"
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM cases")) == 0


@pg
@pytest.mark.parametrize("urgent,level", [
    ({"hospitalizationReported": "YES"}, "CRITICAL"),
    ({"authorityOrCourtDocumentReceived": "YES"}, "CRITICAL"),
    ({"moneyRequested": "YES", "amount": {"amountKopecks": 1_000_000, "currency": "RUB"}},
     "HIGH"),
])
def test_report_free_initial_workflow_keeps_urgent_routing(preparation_workflow, urgent, level):
    core, actor, _, _, engine = preparation_workflow
    workflow = uuid4()
    payload = _payload(eventDate={"date": None, "precision": "UNKNOWN"},
                       safetyScreening=screening(**urgent))
    response = core.post(f"/v1/telegram-case-workflows/{workflow}/submissions",
                         headers=actor_headers(actor), json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert "report" not in body and body["case"]["earlyEscalationId"] is not None
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT level FROM case_escalations WHERE id=:id"),
                                 {"id": body["case"]["earlyEscalationId"]}) == level
        assert connection.scalar(text("SELECT count(*) FROM case_reports")) == 0


@pg
def test_unknown_date_does_not_hide_lost_urgent_policy(preparation_workflow, monkeypatch):
    core, actor, _, _, engine = preparation_workflow

    async def policy_disappeared(*args, **kwargs):
        return None

    monkeypatch.setattr("legal_core.case_api.ensure_early_triage", policy_disappeared)
    response = core.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                         headers=actor_headers(actor), json=_payload(
                             eventDate={"date": None, "precision": "UNKNOWN"},
                             safetyScreening=screening(hospitalizationReported="YES"),
                         ))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "RISK_POLICY_NOT_READY"
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM cases")) == 0


@pg
def test_existing_draft_material_moves_without_raw_copy_or_report(preparation_workflow):
    _, actor, _, _, engine = preparation_workflow
    store = FakeCaseMaterialStore()
    with application_client(case_material_store=store) as core:
        draft = _new_draft(core, actor)
        raw = b"Synthetic anonymised scheduling note."
        uploaded = core.post(f"/v1/telegram-intake-drafts/{draft['id']}/case-materials",
                             headers=_upload_headers(actor, uuid4()), content=raw)
        assert uploaded.status_code == 201, uploaded.text
        objects = deepcopy(store.objects)
        response = core.post(f"/v1/telegram-case-workflows/{draft['id']}/submissions",
                             headers=actor_headers(actor), json=_payload())
        assert response.status_code == 201, response.text
        case = response.json()["case"]["id"]
        assert store.objects == objects and len(objects) == 1
    with engine.connect() as connection:
        material = connection.execute(text("SELECT case_id,draft_id FROM case_materials")).one()
        assert str(material.case_id) == case and material.draft_id is None
        assert connection.scalar(text("SELECT count(*) FROM case_reports")) == 0


@pg
@pytest.mark.parametrize("denial", ["revoked", "expired_subscription"])
def test_access_is_rechecked_after_quota_wait_before_persisting(
    preparation_workflow, denial, monkeypatch,
):
    from legal_core import case_api

    core, actor, clinic, membership, engine = preparation_workflow
    original = case_api._enforce_case_limits

    async def withdraw_after_wait(*args, **kwargs):
        await original(*args, **kwargs)
        with engine.begin() as connection:
            if denial == "revoked":
                connection.execute(text("UPDATE clinic_users SET status='REVOKED' WHERE id=:id"),
                                   {"id": membership})
            else:
                connection.execute(text(
                    "UPDATE subscription_entitlements SET ends_at=clock_timestamp() "
                    "WHERE clinic_id=:id"
                ), {"id": clinic})

    monkeypatch.setattr(case_api, "_enforce_case_limits", withdraw_after_wait)
    response = core.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                         headers=actor_headers(actor), json=_payload())
    assert response.status_code == 403, response.text
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM cases")) == 0
        assert connection.scalar(text("SELECT count(*) FROM telegram_case_workflows")) == 0
