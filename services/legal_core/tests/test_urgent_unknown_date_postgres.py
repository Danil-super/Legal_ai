"""Unknown event dates block dated analysis, never explicit urgent server routing."""

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from telegram_gateway.case_wizard import facts_from_v2_data
from test_case_api import actor_headers, application_client, seed_admin
from test_factual_safety_postgres import guided_data
from test_risk_policy_v3_postgres import isolate_policy_state as isolate_policy_state
from test_risk_policy_v4_postgres import activate_v4, seed_lawyers

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL",
)


@pytest.mark.parametrize("entrypoint", ["workflow", "finalization"])
@pytest.mark.parametrize("signal, level", [
    ({"healthSignals": ["HOSPITALIZATION"]}, "CRITICAL"),
    ({"incomingKind": "AUTHORITY_OR_COURT_DOCUMENT"}, "CRITICAL"),
    ({"safetyScreening": {"moneyRequested": "YES", "amount": {
        "amountKopecks": 1_000_000, "currency": "RUB",
    }}}, "HIGH"),
])
def test_urgent_unknown_date_confirms_replays_and_routes_without_dated_analysis(
    entrypoint, signal, level, monkeypatch,
):
    policy_id = activate_v4()
    actor = 94_000_000_000 + uuid4().int % 1_000_000_000
    foreign = actor + 2_000_000_000
    clinic_id, _membership = seed_admin(actor)
    seed_admin(foreign)
    lawyers = seed_lawyers(clinic_id, actor)
    key = "synthetic-urgent-date-internal-key-1234567890"
    monkeypatch.setenv("AGENT_INTERNAL_KEY", key)
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "0")
    screening = {
        "schemaVersion": SCREENING_VERSION,
        **dict.fromkeys(SCREENING_FIELDS, "UNKNOWN"),
        "amount": "UNKNOWN",
        **signal.get("safetyScreening", {}),
    }
    data = guided_data() | signal | {
        "eventDate": {"date": None, "precision": "UNKNOWN"},
        "safetyScreening": screening,
    }
    facts = facts_from_v2_data(data)
    with application_client() as client:
        if entrypoint == "workflow":
            path = f"/v1/telegram-case-workflows/{uuid4()}/submissions"
            payload = {"intakeSchemaVersion": "dental-case-intake.v2", "facts": facts}
            headers = actor_headers(actor)
            first = client.post(path, headers=headers, json=payload)
            assert first.status_code == 201, first.text
            case = first.json()["case"]
            report = first.json()["report"]["reportJson"]
            assert report["facts"]["EVENT_DATE"] == data["eventDate"]
            assert report["facts"]["FACTUAL_SAFETY_SCREENING"] == screening
            assert report["recommendations"]["status"] == "NOT_AVAILABLE"
            assert report["legalBasis"]["status"] == "NOT_AVAILABLE"
            assert report["draftResponse"]["status"] == "NOT_AVAILABLE"
            assert any(item["factKey"] == "EVENT_DATE" for item in report["missingFacts"])
        else:
            created = client.post(
                "/v1/cases", headers=actor_headers(actor, uuid4()),
                json={"intakeSchemaVersion": "dental-case-intake.v2", "channel": "TELEGRAM"},
            )
            assert created.status_code == 201, created.text
            case_id = created.json()["id"]
            added = client.post(
                f"/v1/cases/{case_id}/facts", headers=actor_headers(actor, uuid4()),
                json={"questionId": "synthetic-urgent-unknown-date",
                      "intakeSchemaVersion": "dental-case-intake.v2", "facts": facts},
            )
            assert added.status_code == 200, added.text
            path = f"/v1/cases/{case_id}/intake-finalizations"
            headers, payload = actor_headers(actor, uuid4()), {}
            first = client.post(path, headers=headers, json=payload)
            assert first.status_code == 200, first.text
            case = first.json()
        assert case["status"] == "ANALYSIS_BLOCKED"
        pointer = case["earlyEscalationId"]
        assert pointer is not None
        replay = client.post(path, headers=headers, json=payload)
        assert replay.status_code == 200 and replay.json() == first.json()
        detail_path = f"/v1/case-escalations/{pointer}"
        detail = client.get(detail_path, headers=actor_headers(actor))
        assert detail.status_code == 200, detail.text
        assert detail.json()["riskLevel"] == level
        assert detail.json()["facts"]["EVENT_DATE"] == data["eventDate"]
        assert detail.json()["facts"]["FACTUAL_SAFETY_SCREENING"] == screening
        assert client.get(detail_path, headers=actor_headers(foreign)).status_code in {403, 404}
        analysis = client.get(
            f"/v1/cases/{case['id']}/analysis-context",
            headers=actor_headers(actor) | {"X-Agent-Internal-Key": key},
        )
        assert analysis.status_code == 422, analysis.text
        assert analysis.json()["error"]["code"] in {"INSUFFICIENT_FACTS", "ANALYSIS_DATE_UNCERTAIN"}
        assert analysis.json()["error"]["details"]["missingFactKeys"] == ["EVENT_DATE"]

    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            routing = connection.execute(text(
                "SELECT e.level,r.policy_id FROM case_escalations e "
                "JOIN case_risk_assessments r ON r.id=e.case_risk_assessment_id "
                "WHERE e.case_id=:case AND e.clinic_id=:clinic"
            ), {"case": UUID(case["id"]), "clinic": clinic_id}).all()
            assert routing == [(level, policy_id)]
            recipients = connection.execute(text(
                "SELECT u.telegram_user_id FROM escalation_notifications n "
                "JOIN clinic_users cu ON cu.id=n.recipient_membership_id "
                "JOIN users u ON u.id=cu.user_id WHERE n.escalation_id=:escalation"
            ), {"escalation": UUID(pointer)}).scalars().all()
            assert len(recipients) == 2 and set(recipients) == lawyers
    finally:
        engine.dispose()
