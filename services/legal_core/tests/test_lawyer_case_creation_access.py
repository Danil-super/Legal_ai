"""Active lawyers create their own cases without acquiring clinic-admin access."""

import os
from uuid import uuid4

import pytest

from telegram_gateway.case_wizard import facts_from_v2_data
from test_case_api import actor_headers, application_client, seed_admin, workflow_submission
from test_case_materials_api import FakeCaseMaterialStore, _new_draft, _upload_headers
from test_escalation_assignment import _add_member, _sql
from test_factual_safety_intake import screening
from test_factual_safety_postgres import guided_data
from test_legal_editor_workspace_api import _seed_user
from test_lawyer_workspace import seed_escalation
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="isolated PostgreSQL",
)


def identifiers():
    owner = 99_000_000_000 + uuid4().int % 100_000_000
    return owner, owner + 1_000_000_000


def guided_payload():
    data = guided_data() | {"safetyScreening": screening(), "caseMaterialsStatus": "ATTACHED"}
    return {"receiptVersion": 2, "intakeSchemaVersion": "dental-case-intake.v2",
            "locale": "ru-RU", "facts": facts_from_v2_data(data)}


def test_lawyer_can_create_owned_guided_case_with_material_and_resumable_receipt(
    isolated_database,
):
    owner, lawyer = identifiers()
    clinic, _ = seed_admin(owner, role="CLINIC_OWNER")
    with application_client(case_material_store=FakeCaseMaterialStore()) as client:
        membership = _add_member(client, owner, clinic, lawyer, "CLINIC_LAWYER")
        draft = _new_draft(client, lawyer)
        path = f"/v1/telegram-intake-drafts/{draft['id']}"
        assert client.get(path, headers=actor_headers(owner)).status_code == 404
        raw = b"Synthetic anonymised clinic note: a review appointment was offered."
        uploaded = client.post(path + "/case-materials", content=raw,
                               headers=_upload_headers(lawyer, uuid4()),
                               params={"include_in_analysis": "true"})
        assert uploaded.status_code == 201, uploaded.text
        material = uploaded.json()["material"]["id"]
        assert uploaded.json()["material"]["analysisUseAllowed"] is True
        workflow_path = f"/v1/telegram-case-workflows/{draft['id']}/submissions"
        created = client.post(workflow_path, headers=actor_headers(lawyer), json=guided_payload())
        assert created.status_code == 201, created.text
        case = created.json()["case"]["id"]
        assert created.json()["receiptVersion"] == 2
        assert client.get(f"/v1/cases/{case}", headers=actor_headers(lawyer)).status_code == 200
        intake = client.get(f"/v1/cases/{case}/intake", headers=actor_headers(lawyer))
        assert intake.status_code == 200, intake.text
        downloaded = client.get(f"/v1/cases/{case}/case-materials/{material}",
                                headers=actor_headers(lawyer))
        assert downloaded.status_code == 200 and downloaded.content == raw
        replay = client.post(workflow_path, headers=actor_headers(lawyer), json=guided_payload())
        assert replay.status_code == 200 and replay.json() == created.json()
        row = _sql("SELECT created_by_membership_id FROM cases WHERE id=CAST(:case AS uuid)",
                   case=case)[0]
        assert row["created_by_membership_id"] == membership
        assert client.get("/v1/clinic/members", headers=actor_headers(lawyer)).status_code == 403


def test_lawyer_can_open_own_saved_report_but_not_another_same_clinic_case(
    isolated_database,
):
    owner, lawyer = identifiers()
    clinic, _ = seed_admin(owner, role="CLINIC_OWNER")
    with application_client() as client:
        _add_member(client, owner, clinic, lawyer, "CLINIC_LAWYER")
        own = client.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                          headers=actor_headers(lawyer), json=workflow_submission())
        assert own.status_code == 201, own.text
        own_report = own.json()["report"]["id"]
        pdf = client.get(f"/v1/reports/{own_report}/pdf", headers=actor_headers(lawyer))
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
        other = client.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                            headers=actor_headers(owner), json=workflow_submission())
        assert other.status_code == 201, other.text
        case, report = other.json()["case"]["id"], other.json()["report"]["id"]
        for path in (f"/v1/cases/{case}", f"/v1/cases/{case}/intake",
                     f"/v1/reports/{report}/pdf"):
            denied = client.get(path, headers=actor_headers(lawyer))
            assert denied.status_code in {403, 404}, (path, denied.text)


def test_same_clinic_lawyer_cannot_read_unescalated_case_before_creating_anything(
    isolated_database,
):
    owner, lawyer = identifiers()
    clinic, _ = seed_admin(owner, role="CLINIC_OWNER")
    with application_client() as client:
        _add_member(client, owner, clinic, lawyer, "CLINIC_LAWYER")
        created = client.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
                              headers=actor_headers(owner), json=workflow_submission())
        assert created.status_code == 201, created.text
        case, report = created.json()["case"]["id"], created.json()["report"]["id"]
        for path in (f"/v1/cases/{case}", f"/v1/reports/{report}/pdf"):
            response = client.get(path, headers=actor_headers(lawyer))
            assert response.status_code in {403, 404}, (path, response.text)


def test_escalated_case_stays_readable_without_granting_intake_mutation(isolated_database):
    owner, escalation, clinic = seed_escalation()
    lawyer = owner + 2_000_000_000
    case = _sql("SELECT case_id FROM case_escalations WHERE id=:id", id=escalation)[0]["case_id"]
    with application_client() as client:
        _add_member(client, owner, clinic, lawyer, "CLINIC_LAWYER")
        assert client.get(f"/v1/case-escalations/{escalation}",
                          headers=actor_headers(lawyer)).status_code == 200
        assert client.get(f"/v1/cases/{case}", headers=actor_headers(lawyer)).status_code == 200
        from test_case_api import complete_fact_batch

        mutation = client.post(f"/v1/cases/{case}/facts",
                               headers=actor_headers(lawyer, uuid4()), json=complete_fact_batch())
        assert mutation.status_code in {403, 404}, mutation.text


@pytest.mark.parametrize("state", ["membership-revoked", "user-blocked", "no-subscription"])
def test_inactive_lawyer_cannot_create_a_draft(isolated_database, state):
    _, lawyer = identifiers()
    _, membership = seed_admin(
        lawyer, role="CLINIC_LAWYER", entitlement_status=None if state == "no-subscription"
        else "ACTIVE",
    )
    if state == "membership-revoked":
        _sql("UPDATE clinic_users SET status='REVOKED' WHERE id=:id", id=membership)
    elif state == "user-blocked":
        _sql("UPDATE users SET status='BLOCKED' WHERE telegram_user_id=:actor", actor=lawyer)
    with application_client() as client:
        response = client.post("/v1/telegram-intake-drafts",
                               headers=actor_headers(lawyer, uuid4()),
                               json={"intakeSchemaVersion": "dental-case-intake.v2"})
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == (
        "SUBSCRIPTION_INACTIVE" if state == "no-subscription" else "ACTOR_NOT_AUTHORIZED"
    )


def test_legal_editor_without_clinic_membership_cannot_create_cases(isolated_database):
    _, editor = identifiers()
    _seed_user(editor, system_role="LEGAL_EDITOR")
    with application_client() as client:
        response = client.post("/v1/telegram-intake-drafts",
                               headers=actor_headers(editor, uuid4()),
                               json={"intakeSchemaVersion": "dental-case-intake.v2"})
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ACTOR_NOT_AUTHORIZED"
