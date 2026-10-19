"""Urgent confirmed facts outrank unknown dates/safety and absent legal evidence."""

import pytest
from sqlalchemy import text
from uuid import uuid4

from legal_core.analysis_contracts import PreparationResult
from telegram_gateway.case_wizard import facts_from_v2_data
from test_case_api import actor_headers
from test_factual_safety_intake import screening
from test_factual_safety_postgres import guided_data
from test_preparation_precontext import isolated_database as isolated_database
from test_preparation_precontext import frozen_precontext as frozen_precontext
from test_preparation_precontext import pytestmark as pytestmark
from test_preparation_analysis import _submission
from test_preparation_analysis import prepared_case as prepared_case


@pytest.mark.parametrize(("frozen_precontext", "level"), [
    ({"eventDate": {"date": None, "precision": "UNKNOWN"},
      "healthSignals": ["HOSPITALIZATION"],
      "safetyScreening": screening(hospitalizationReported="YES", representativeContact="UNKNOWN")},
     "CRITICAL"),
    ({"eventDate": {"date": None, "precision": "UNKNOWN"},
      "incomingKind": "AUTHORITY_OR_COURT_DOCUMENT",
      "safetyScreening": screening(authorityOrCourtDocumentReceived="YES",
                                   representativeContact="UNKNOWN")}, "CRITICAL"),
    ({"eventDate": {"date": None, "precision": "UNKNOWN"},
      "safetyScreening": screening(moneyRequested="YES", representativeContact="UNKNOWN",
                                   amount={"amountKopecks": 1_000_000, "currency": "RUB"})},
     "HIGH"),
], indirect=["frozen_precontext"])
def test_urgent_precontext_stores_actual_escalation_before_optional_gates(frozen_precontext, level):
    client, _, case, headers, engine = frozen_precontext
    path = f"/v1/cases/{case}/preparation-context"
    first = client.post(path, headers=headers)
    assert first.status_code == 201, first.text
    result = PreparationResult.model_validate(first.json()).root
    assert result.status == "HUMAN_REVIEW" and result.risk_level == level
    assert result.risk_policy_version == "dental-risk.v4"
    assert result.source.context is None
    replay = client.post(path, headers=headers)
    assert replay.status_code == 200 and replay.json() == first.json()
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT case_id,level FROM case_escalations WHERE id=:id"
        ), {"id": result.escalation_id}).one() == (result.source.case_id, level)
        assert connection.scalar(text("SELECT status FROM cases WHERE id=:id"),
                                 {"id": case}) == "ESCALATION_REQUIRED"
        for table, count in (("case_reports", 0), ("case_analysis_runs", 0),
                             ("case_risk_assessments", 1)):
            assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE case_id=:id"),
                                     {"id": case}) == count


@pytest.mark.parametrize("ready", [False, True])
def test_ready_context_does_not_reserve_the_late_submission_key(prepared_case, ready):
    client, _, _, case, headers, _, engine = prepared_case
    context = client.post(f"/v1/cases/{case}/preparation-context", headers=headers)
    assert context.status_code == 200, context.text
    assert "source" not in context.json()
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM idempotency_records WHERE scope=:scope AND key=:key"
        ), {"scope": f"cases:{case}:preparation-submissions",
            "key": headers["Idempotency-Key"]}) == 0
    submitted = client.post(f"/v1/cases/{case}/preparation-submissions", headers=headers,
                            json=_submission(context.json(), questions=not ready, claims=ready))
    assert submitted.status_code == 201, submitted.text
    assert submitted.json()["status"] == ("READY" if ready else "NEEDS_FACTS")
    assert submitted.json()["source"]["context"] is not None
    repeated = client.post(f"/v1/cases/{case}/preparation-context", headers=headers)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == submitted.json()


@pytest.mark.parametrize("frozen_precontext", [{
    "healthSignals": ["HOSPITALIZATION"],
    "safetyScreening": screening(hospitalizationReported="YES"),
}], indirect=True)
def test_operator_stop_refuses_new_preparation_without_making_a_risk_decision(
    frozen_precontext, monkeypatch,
):
    client, _, case, headers, engine = frozen_precontext
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    response = client.post(f"/v1/cases/{case}/preparation-context", headers=headers)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "ANALYSIS_SAFE_STOP"
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM case_risk_assessments WHERE case_id=:id"
        ), {"id": case}) == 0


def test_operator_stop_preserves_existing_urgent_intake_and_lawyer_routing(
    frozen_precontext, monkeypatch,
):
    client, actor, _, headers, engine = frozen_precontext
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    data = guided_data() | {
        "eventDate": {"date": None, "precision": "UNKNOWN"},
        "healthSignals": ["HOSPITALIZATION"],
        "safetyScreening": screening(hospitalizationReported="YES"),
    }
    created = client.post("/v1/cases", headers=actor_headers(actor, uuid4()), json={
        "intakeSchemaVersion": "dental-case-intake.v2", "channel": "TELEGRAM",
    })
    assert created.status_code == 201, created.text
    case = created.json()["id"]
    added = client.post(f"/v1/cases/{case}/facts", headers=actor_headers(actor, uuid4()), json={
        "questionId": "synthetic-urgent-stop", "intakeSchemaVersion": "dental-case-intake.v2",
        "facts": facts_from_v2_data(data),
    })
    assert added.status_code == 200, added.text
    finalized = client.post(f"/v1/cases/{case}/intake-finalizations",
                            headers=actor_headers(actor, uuid4()), json={})
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["earlyEscalationId"] is not None
    stopped = client.post(f"/v1/cases/{case}/preparation-context", headers=headers)
    assert stopped.status_code == 503, stopped.text
    with engine.connect() as connection:
        for table, expected in (("case_escalations", 1), ("case_risk_assessments", 1),
                                ("case_reports", 0), ("case_analysis_runs", 0)):
            assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE case_id=:id"),
                                     {"id": case}) == expected
