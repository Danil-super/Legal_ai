"""Disposable PG proof of factual draft isolation and both urgent confirmation routes."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.database import database_url
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from legal_core.risk_policy_approval import RiskPolicyApproval, approve_risk_policy
from telegram_gateway.case_wizard import facts_from_v2_data
from test_case_api import actor_headers, application_client, seed_admin
from test_legal_editor_workspace_api import _seed_user
from test_verified_copy_analysis_e2e import isolated_database, pytestmark  # noqa: F401


def guided_data() -> dict:
    return {
        "intakeVersion": 2,
        "incomingKind": "COMPLAINT",
        "incomingSourceStatus": "NOT_ATTACHED",
        "situationAreas": ["SERVICE"],
        "eventSummary": "Вымышленное обращение для проверки фактических уточнений.",
        "eventDate": {"date": "2026-09-12", "precision": "EXACT"},
        "conflictStage": "FIRST",
        "clinicActions": ["NOTHING_YET"],
        "healthSignals": ["NO_KNOWN_INFORMATION"],
        "caseMaterialsStatus": "NOT_ATTACHED",
    }


@pytest.mark.usefixtures("isolated_database")
def test_partial_screening_is_resumable_revision_guarded_and_tenant_private():
    actor = 75_000_000_000 + uuid4().int % 1_000_000_000
    other = actor + 2_000_000_000
    seed_admin(actor)
    seed_admin(other)
    data = guided_data() | {
        "safetyScreening": {
            "schemaVersion": SCREENING_VERSION,
            "healthDeteriorationReported": "UNKNOWN",
        }
    }
    with application_client() as client:
        created = client.post(
            "/v1/telegram-intake-drafts",
            headers=actor_headers(actor, uuid4()),
            json={"intakeSchemaVersion": "dental-case-intake.v2"},
        )
        assert created.status_code == 201, created.text
        draft_id = created.json()["id"]
        path = f"/v1/telegram-intake-drafts/{draft_id}"
        payload = {"expectedRevision": 1, "wizardState": "SAFETY", "draftData": data}
        saved = client.put(path, headers=actor_headers(actor, uuid4()), json=payload)
        assert saved.status_code == 200, saved.text
        resumed = client.get(path, headers=actor_headers(actor))
        assert resumed.json()["draftData"] == data
        assert resumed.json()["wizardState"] == "SAFETY"
        assert resumed.json()["revision"] == 2
        assert client.get(path, headers=actor_headers(other)).status_code in {403, 404}
        denied = client.put(path, headers=actor_headers(other, uuid4()), json=payload)
        assert denied.status_code in {403, 404}
        assert (
            client.put(path, headers=actor_headers(actor, uuid4()), json=payload).status_code == 409
        )
        malformed = data | {
            "safetyScreening": {
                "schemaVersion": SCREENING_VERSION,
                "representativeContact": ["NO"],
            }
        }
        invalid = client.put(
            path,
            headers=actor_headers(actor, uuid4()),
            json=payload
            | {
                "expectedRevision": 2,
                "draftData": malformed,
            },
        )
        assert invalid.status_code == 422
        assert client.get(path, headers=actor_headers(actor)).json()["draftData"] == data


@pytest.mark.parametrize("entrypoint", ["workflow", "finalization"])
@pytest.mark.usefixtures("isolated_database")
@pytest.mark.parametrize(
    ("field", "level"),
    [
        ("hospitalizationReported", "CRITICAL"),
        ("writtenRequirementsReceived", "HIGH"),
    ],
)
def test_factual_positive_routes_without_evidence_and_unknowns_never_suppress_urgency(
    entrypoint,
    field,
    level,
    monkeypatch,
):
    actor = 75_000_000_000 + uuid4().int % 1_000_000_000
    editor = actor + 1_000_000_000
    other = actor + 2_000_000_000
    seed_admin(actor)
    seed_admin(other)
    _seed_user(editor, system_role="LEGAL_EDITOR")

    async def reviewed_candidate():
        engine = create_async_engine(database_url())
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        values = {
            "reviewer_telegram_user_id": editor,
            "high_demand_threshold_kopecks": 5_000_000,
            "incident_triggers_reviewed": True,
            "monetary_threshold_reviewed": True,
            "escalation_rules_reviewed": True,
        }
        try:
            await approve_risk_policy(sessions, RiskPolicyApproval(**values))
            await approve_risk_policy(
                sessions,
                RiskPolicyApproval(
                    **values,
                    version=3,
                    early_triage_enabled=True,
                    supersede_approved=True,
                    guided_v2_explicit_signals_enabled=True,
                    direct_v1_supersession_reviewed=True,
                    factual_safety_intake_enabled=True,
                    factual_safety_intake_reviewed=True,
                ),
            )
        finally:
            await engine.dispose()

    asyncio.run(reviewed_candidate())
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    data = guided_data() | {
        "safetyScreening": {
            "schemaVersion": SCREENING_VERSION,
            **dict.fromkeys(SCREENING_FIELDS, "UNKNOWN"),
            field: "YES",
            "amount": "UNKNOWN",
        }
    }
    facts = facts_from_v2_data(data)
    with application_client() as client:
        if entrypoint == "workflow":
            path = f"/v1/telegram-case-workflows/{uuid4()}/submissions"
            payload = {
                "intakeSchemaVersion": "dental-case-intake.v2",
                "locale": "ru-RU",
                "facts": facts,
            }
            headers = actor_headers(actor)
            first = client.post(path, headers=headers, json=payload)
            assert first.status_code == 201, first.text
            pointer = first.json()["case"]["earlyEscalationId"]
        else:
            created = client.post(
                "/v1/cases",
                headers=actor_headers(actor, uuid4()),
                json={
                    "intakeSchemaVersion": "dental-case-intake.v2",
                    "channel": "TELEGRAM",
                },
            )
            assert created.status_code == 201, created.text
            case_id = created.json()["id"]
            added = client.post(
                f"/v1/cases/{case_id}/facts",
                headers=actor_headers(actor, uuid4()),
                json={
                    "questionId": "synthetic-factual-screening",
                    "intakeSchemaVersion": "dental-case-intake.v2",
                    "facts": facts,
                },
            )
            assert added.status_code == 200, added.text
            path = f"/v1/cases/{case_id}/intake-finalizations"
            headers = actor_headers(actor, uuid4())
            payload = {}
            first = client.post(path, headers=headers, json=payload)
            assert first.status_code == 200, first.text
            pointer = first.json()["earlyEscalationId"]
        replay = client.post(path, headers=headers, json=payload)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert pointer is not None
        detail = client.get(f"/v1/case-escalations/{pointer}", headers=actor_headers(actor))
        assert detail.status_code == 200, detail.text
        assert detail.json()["riskLevel"] == level
        assert detail.json()["facts"]["FACTUAL_SAFETY_SCREENING"][field] == "YES"
        assert detail.json()["facts"]["FACTUAL_SAFETY_SCREENING"]["amount"] == "UNKNOWN"
        assert client.get(
            f"/v1/case-escalations/{pointer}", headers=actor_headers(other)
        ).status_code in {403, 404}
