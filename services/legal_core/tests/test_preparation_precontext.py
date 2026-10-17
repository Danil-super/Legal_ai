"""Core limitations before model/retrieval; all intake data are fictional."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.analysis_contracts import PreparationResult
from legal_core.database import owner_database_url
from telegram_gateway.case_wizard import facts_from_v2_data
from test_case_api import actor_headers, application_client, seed_admin
from test_factual_safety_intake import screening
from test_factual_safety_postgres import guided_data
from test_risk_policy_v4_postgres import activate_v4
from test_verified_copy_analysis_e2e import INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.fixture
def frozen_precontext(isolated_database, request):
    actor = 81_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    activate_v4()
    data = guided_data() | {"safetyScreening": screening()} | getattr(request, "param", {})
    engine = create_engine(owner_database_url())
    try:
        with application_client() as client:
            headers = {**actor_headers(actor, uuid4()), "X-Agent-Internal-Key": INTERNAL_KEY}
            created = client.post("/v1/cases", headers=headers, json={
                "intakeSchemaVersion": "dental-case-intake.v2", "channel": "TELEGRAM",
            })
            assert created.status_code == 201, created.text
            case = created.json()["id"]
            added = client.post(f"/v1/cases/{case}/facts", headers=headers, json={
                "questionId": "synthetic-precontext",
                "intakeSchemaVersion": "dental-case-intake.v2",
                "facts": facts_from_v2_data(data),
            })
            assert added.status_code == 200, added.text
            # Seed the frozen-state boundary for the future V2 initial producer.
            # Legacy finalization still rejects unknown dates; this fixture does
            # not claim that the unchanged Telegram producer can submit them yet.
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE cases SET status='ANALYSIS_BLOCKED',closed_at=clock_timestamp(),"
                    "retention_due_at=clock_timestamp()+interval '30 days' WHERE id=:id"
                ), {"id": case})
            yield client, actor, case, headers, engine
    finally:
        engine.dispose()


@pytest.mark.parametrize(("frozen_precontext", "reason"), [
    ({"eventDate": {"date": None, "precision": "UNKNOWN"}}, "ANALYSIS_DATE_UNCERTAIN"),
    ({"eventDate": {"date": "2026-09-01", "precision": "APPROXIMATE"}},
     "ANALYSIS_DATE_UNCERTAIN"),
    ({"safetyScreening": screening(hospitalizationReported="UNKNOWN")},
     "FACTUAL_SAFETY_UNCERTAIN"),
    ({}, "LEGAL_EVIDENCE_UNAVAILABLE"),
], indirect=["frozen_precontext"])
def test_precontext_limits_have_real_operations_without_verifier_or_report(
    frozen_precontext, reason, monkeypatch,
):
    client, _, case, headers, engine = frozen_precontext
    monkeypatch.setattr("legal_core.analysis_api.render_report_pdf",
                        lambda *args: pytest.fail("pre-context stop must not render a report"))
    path = f"/v1/cases/{case}/preparation-context"
    first = client.post(path, headers=headers)
    assert first.status_code == 201, first.text
    result = PreparationResult.model_validate(first.json()).root
    assert result.status == "LIMITED" and result.reason_code == reason
    assert result.source.context is None
    assert "report" not in first.json()
    replay = client.post(path, headers=headers)
    assert replay.status_code == 200 and replay.json() == first.json()
    with engine.connect() as connection:
        record = connection.execute(text(
            "SELECT id,resource_type,resource_id FROM idempotency_records "
            "WHERE scope=:scope AND key=:key"
        ), {"scope": f"cases:{case}:preparation-submissions",
            "key": headers["Idempotency-Key"]}).one()
        assert record.id == result.source.operation_id
        assert record.resource_type == "CASE_PREPARATION"
        assert str(record.resource_id) == case
        for table in ("case_reports", "case_analysis_runs", "case_risk_assessments"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE case_id=:id"),
                                     {"id": case}) == 0


def test_precontext_does_not_turn_auth_failure_into_a_factual_limitation(frozen_precontext):
    client, _, case, headers, _ = frozen_precontext
    response = client.post(f"/v1/cases/{case}/preparation-context",
                           headers=headers | {"X-Agent-Internal-Key": "x" * 32})
    assert response.status_code == 403
    assert "source" not in response.json()


@pytest.mark.parametrize(("status", "code"), [
    (403, "ACTOR_NOT_AUTHORIZED"), (409, "RISK_POLICY_NOT_READY"),
    (503, "ANALYSIS_STOPPED"), (500, "UNEXPECTED_INTERNAL_FAILURE"),
    (422, "FACTUAL_SAFETY_SCREENING_INVALID"),
])
def test_only_explicit_core_blockers_become_limits(frozen_precontext, monkeypatch, status, code):
    from legal_core.case_api import ApiError

    client, _, case, headers, engine = frozen_precontext

    async def refusal(*args):
        raise ApiError(status_code=status, code=code, message="Synthetic bounded refusal")

    monkeypatch.setattr("legal_core.analysis_api._load_analysis_state", refusal)
    response = client.post(f"/v1/cases/{case}/preparation-context", headers=headers)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM idempotency_records WHERE scope=:scope"
        ), {"scope": f"cases:{case}:preparation-submissions"}) == 0
