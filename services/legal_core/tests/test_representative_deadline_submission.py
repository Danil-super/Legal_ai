"""The gateway's deadline mapping must pass the real authorized workflow submission."""

import os
from uuid import uuid4

import pytest
from telegram_gateway.case_wizard import CaseDraft, facts_from_draft
from test_case_api import actor_headers, application_client, seed_admin

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL",
)


@pytest.mark.parametrize("all_sources", [False, True])
@pytest.mark.parametrize("unknown", [False, True])
def test_representative_deadline_survives_real_core_submission(all_sources, unknown):
    actor = 6_500_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    deadline = {"date": None if unknown else "2026-09-30",
                "precision": "UNKNOWN" if unknown else "EXACT"}
    draft = CaseDraft(
        incident_type="QUALITY_COMPLAINT", service_type="Установка коронки",
        service_date="2026-06-01", incident_date="2026-07-01", claim_date="2026-07-02",
        problem_summary="Синтетический кейс: представитель попросил уточнить обстоятельства.",
        patient_demand="NO_SPECIFIC_DEMAND", formal_claim=all_sources,
        harm_claimed="NO", regulator_or_court=all_sources, documents_status="COMPLETE",
        lawyer_contact="YES", representative_authority="UNKNOWN", response_deadline=deadline,
        claim_received_at="2026-07-02" if all_sources else None,
        authority_kind="Суд" if all_sources else None,
        authority_document_date="2026-07-03" if all_sources else None,
    )
    facts = facts_from_draft(draft)
    deadline_facts = [fact for fact in facts if fact["factKey"] == "RESPONSE_DEADLINE"]
    assert len(deadline_facts) == 1 and deadline_facts[0]["value"] == deadline
    body = {"intakeSchemaVersion": "dental-case-intake.v1", "locale": "ru-RU", "facts": facts}
    workflow_id = uuid4()
    with application_client() as client:
        response = client.post(
            f"/v1/telegram-case-workflows/{workflow_id}/submissions",
            headers=actor_headers(actor), json=body,
        )
        assert response.status_code in {200, 201}, response.text
        report = response.json()["report"]
        assert report["reportJson"]["missingFacts"] == []
        # Same workflow, same facts: no new report on the retry.
        replay = client.post(
            f"/v1/telegram-case-workflows/{workflow_id}/submissions",
            headers=actor_headers(actor), json=body,
        )
        assert replay.status_code in {200, 201}, replay.text
        assert replay.json()["report"]["id"] == report["id"]
