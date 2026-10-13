"""Synthetic PostgreSQL contract tests for immutable factual clarification recovery."""

import asyncio
import os
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.database import database_url, owner_database_url
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from legal_core.risk_policy_approval import RiskPolicyApproval, approve_risk_policy
from legal_core.runtime_db_role import provision_runtime_role
from telegram_gateway.case_wizard import facts_from_v2_data
from test_case_api import actor_headers, application_client, seed_admin
from test_case_materials_api import FakeCaseMaterialStore, _new_draft, _upload_headers
from test_legal_editor_workspace_api import _seed_user

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)
INTERNAL_KEY = "synthetic-clarification-internal-" + "x" * 32


@pytest.fixture(scope="module", autouse=True)
def factual_policy():
    """Approve synthetic policy only inside the caller's disposable test database."""
    assert os.environ["POSTGRES_DB"].startswith("dental_legal_test_")
    identifier = "dental_legal_test_clarify_" + uuid4().hex[:16]
    owner = create_engine(owner_database_url(), isolation_level="AUTOCOMMIT")
    with owner.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{identifier}"')
    patch = pytest.MonkeyPatch()
    patch.setenv("POSTGRES_DB", identifier)
    provision_runtime_role()
    command.upgrade(Config(str(Path(__file__).parents[3] / "alembic.ini")), "head")
    provision_runtime_role()
    editor = 92_000_000_000 + uuid4().int % 100_000_000
    _seed_user(editor, system_role="LEGAL_EDITOR")

    async def approve():
        engine = create_async_engine(database_url())
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            for version in (1, 3):
                await approve_risk_policy(sessions, RiskPolicyApproval(
                    reviewer_telegram_user_id=editor,
                    version=version,
                    high_demand_threshold_kopecks=5_000_000,
                    incident_triggers_reviewed=True,
                    monetary_threshold_reviewed=True,
                    escalation_rules_reviewed=True,
                    early_triage_enabled=version == 3,
                    supersede_approved=version == 3,
                    guided_v2_explicit_signals_enabled=version == 3,
                    direct_v1_supersession_reviewed=version == 3,
                    factual_safety_intake_enabled=version == 3,
                    factual_safety_intake_reviewed=version == 3,
                ))
        finally:
            await engine.dispose()

    try:
        asyncio.run(approve())
        yield
    finally:
        patch.undo()
        with owner.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{identifier}" WITH (FORCE)')
        owner.dispose()


def _guided_data() -> dict:
    return {
        "intakeVersion": 2,
        "incomingKind": "COMPLAINT",
        "incomingSourceStatus": "NOT_ATTACHED",
        "situationAreas": ["TREATMENT"],
        "affectedServices": ["синтетическая установка коронки"],
        "eventSummary": "Синтетический пациент сообщил о сколе конструкции.",
        "eventDate": {"date": "2026-09-12", "precision": "EXACT"},
        "conflictStage": "FIRST",
        "clinicActions": ["INVITED_FOR_EXAMINATION"],
        "healthSignals": ["NO_KNOWN_INFORMATION"],
        "caseMaterialsStatus": "NOT_ATTACHED",
        "safetyScreening": {
            "schemaVersion": SCREENING_VERSION,
            **dict.fromkeys(SCREENING_FIELDS, "NO"),
            "amount": "NOT_REQUESTED",
        },
    }


def _confirm(client, actor: int, data: dict, workflow_id: UUID | None = None) -> dict:
    response = client.post(
        f"/v1/telegram-case-workflows/{workflow_id or uuid4()}/submissions",
        headers=actor_headers(actor),
        json={"intakeSchemaVersion": "dental-case-intake.v2", "locale": "ru-RU",
              "facts": facts_from_v2_data(data)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _source_hash(workflow: dict) -> str:
    return workflow["report"]["reportJson"]["factSnapshotSha256"]


def _source_rows(case_id: str) -> dict:
    """Include content and ownership, so an accidental move/copy cannot pass."""
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            return {
                table: connection.execute(text(
                    f"SELECT to_jsonb(r) FROM {table} r WHERE {column}=:id ORDER BY id"
                ), {"id": case_id}).scalars().all()
                for table, column in (
                    ("cases", "id"), ("case_facts", "case_id"),
                    ("case_reports", "case_id"), ("case_materials", "case_id"),
                    ("analysis_jobs", "case_id"),
                )
            }
    finally:
        engine.dispose()


def _request_draft(client, actor: int, workflow: dict, key: UUID | None = None):
    return client.post(
        f"/v1/cases/{workflow['case']['id']}/clarification-drafts",
        json={"expectedFactSnapshotSha256": _source_hash(workflow)},
        headers=actor_headers(actor, key or uuid4()),
    )


@pytest.mark.parametrize("unknown_field", [*SCREENING_FIELDS, "amount", "absent"])
def test_unknown_factual_safety_blocks_before_evidence_retrieval(
    monkeypatch: pytest.MonkeyPatch, unknown_field: str,
) -> None:
    actor = 93_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    monkeypatch.setenv("AGENT_INTERNAL_KEY", INTERNAL_KEY)
    retrieval_calls = []

    async def empty_evidence(*args, **kwargs):
        retrieval_calls.append(True)
        return []

    monkeypatch.setattr("legal_core.analysis_api.retrieve_planned_evidence", empty_evidence)
    data = _guided_data()
    if unknown_field == "absent":
        data.pop("safetyScreening")
        expected = "FACTUAL_SAFETY_SCREENING_UNKNOWN"
    elif unknown_field == "amount":
        data["safetyScreening"].update(moneyRequested="YES", amount="UNKNOWN")
        expected = "FACTUAL_SAFETY_AMOUNT_UNKNOWN"
    else:
        data["safetyScreening"][unknown_field] = "UNKNOWN"
        if unknown_field == "moneyRequested":
            data["safetyScreening"]["amount"] = "UNKNOWN"
        expected = f"FACTUAL_SAFETY_{unknown_field.upper()}_UNKNOWN"
    with application_client() as client:
        workflow = _confirm(client, actor, data)
        before = _source_rows(workflow["case"]["id"])
        response = client.get(
            f"/v1/cases/{workflow['case']['id']}/analysis-context",
            headers={**actor_headers(actor), "X-Agent-Internal-Key": INTERNAL_KEY},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == expected
        assert retrieval_calls == []
        assert _source_rows(workflow["case"]["id"]) == before


@pytest.mark.parametrize("urgent_field", [
    "hospitalizationReported", "representativeContact", "writtenRequirementsReceived",
])
def test_known_urgent_signal_survives_unrelated_unknown_screening(
    monkeypatch: pytest.MonkeyPatch, urgent_field: str,
) -> None:
    actor = 93_500_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    monkeypatch.setenv("AGENT_INTERNAL_KEY", INTERNAL_KEY)
    retrieval_calls = []

    async def empty_evidence(*args, **kwargs):
        retrieval_calls.append(True)
        return []

    monkeypatch.setattr("legal_core.analysis_api.retrieve_planned_evidence", empty_evidence)
    data = _guided_data()
    data["safetyScreening"].update(dict.fromkeys(SCREENING_FIELDS, "UNKNOWN"))
    data["safetyScreening"].update({urgent_field: "YES", "amount": "UNKNOWN"})
    with application_client() as client:
        workflow = _confirm(client, actor, data)
        assert workflow["case"]["earlyEscalationId"] is not None
        response = client.get(
            f"/v1/cases/{workflow['case']['id']}/analysis-context",
            headers={**actor_headers(actor), "X-Agent-Internal-Key": INTERNAL_KEY},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "LEGAL_EVIDENCE_UNAVAILABLE"
        assert retrieval_calls == [True]


@pytest.mark.parametrize("precision,expected_state", [
    ("EXACT", "SAFETY"), ("UNKNOWN", "EVENT_DATE"), ("APPROXIMATE", "EVENT_DATE"),
])
def test_clarification_prefills_a_fresh_unconfirmed_draft_and_replays(
    precision: str, expected_state: str,
) -> None:
    actor = 94_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    data = _guided_data()
    data["safetyScreening"]["representativeContact"] = "UNKNOWN"
    data["eventDate"] = {"precision": precision,
                         "date": None if precision == "UNKNOWN" else "2026-09-12"}
    if precision != "EXACT":
        # Existing confirmation admits an uncertain date only for known urgent routing.
        data["healthSignals"] = ["HOSPITALIZATION"]
        data["safetyScreening"]["hospitalizationReported"] = "YES"
    with application_client() as client:
        workflow = _confirm(client, actor, data)
        before = _source_rows(workflow["case"]["id"])
        key = uuid4()
        response = _request_draft(client, actor, workflow, key)
        assert response.status_code == 201, response.text
        draft = response.json()
        assert UUID(draft["id"]) != UUID(workflow["workflowId"])
        assert draft["wizardState"] == expected_state
        prefilled = deepcopy(data)
        prefilled["safetyScreening"].pop("representativeContact")
        assert draft["draftData"] == {
            **prefilled, "correctionSource": {"caseId": workflow["case"]["id"],
                                         "factSnapshotSha256": _source_hash(workflow)},
        }
        replay = _request_draft(client, actor, workflow, key)
        assert replay.status_code == 200, replay.text
        assert replay.json() == draft
        readback = client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                              headers=actor_headers(actor))
        assert readback.json() == draft
        assert _source_rows(workflow["case"]["id"]) == before
        listing = client.get("/v1/telegram-intake-drafts", headers=actor_headers(actor))
        assert [item["id"] for item in listing.json()["items"]] == [draft["id"]]
        mismatch = client.post(
            f"/v1/cases/{workflow['case']['id']}/clarification-drafts",
            headers=actor_headers(actor, key),
            json={"expectedFactSnapshotSha256": "0" * 64},
        )
        assert mismatch.status_code == 422, mismatch.text
        assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.parametrize("tampering", ["remove", "caseId", "factSnapshotSha256"])
def test_correction_source_is_preserved_on_save_and_cannot_be_rewritten(tampering: str) -> None:
    actor = 95_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    data = _guided_data()
    data["safetyScreening"]["representativeContact"] = "UNKNOWN"
    with application_client() as client:
        workflow = _confirm(client, actor, data)
        created = _request_draft(client, actor, workflow)
        assert created.status_code == 201, created.text
        draft = created.json()
        corrected = deepcopy(draft["draftData"])
        corrected["safetyScreening"]["representativeContact"] = "NO"
        saved = client.put(f"/v1/telegram-intake-drafts/{draft['id']}",
                           headers=actor_headers(actor, uuid4()),
                           json={"expectedRevision": draft["revision"],
                                 "wizardState": "SUMMARY", "draftData": corrected})
        assert saved.status_code == 200, saved.text
        assert saved.json()["draftData"]["correctionSource"] == (
            draft["draftData"]["correctionSource"]
        )
        forged = deepcopy(saved.json()["draftData"])
        if tampering == "remove":
            forged.pop("correctionSource")
        else:
            forged["correctionSource"][tampering] = (
                str(uuid4()) if tampering == "caseId" else "0" * 64
            )
        rejected = client.put(f"/v1/telegram-intake-drafts/{draft['id']}",
                              headers=actor_headers(actor, uuid4()),
                              json={"expectedRevision": saved.json()["revision"],
                                    "wizardState": "SUMMARY", "draftData": forged})
        assert rejected.status_code == 409, rejected.text
        readback = client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                              headers=actor_headers(actor))
        assert readback.json() == saved.json()


def test_clarification_never_copies_or_moves_confirmed_materials() -> None:
    actor = 96_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    store = FakeCaseMaterialStore()
    data = _guided_data()
    data.update(incomingSourceStatus="ATTACHED", caseMaterialsStatus="ATTACHED")
    data["safetyScreening"]["representativeContact"] = "UNKNOWN"
    with application_client(case_material_store=store) as client:
        original = _new_draft(client, actor)
        uploaded = client.post(
            f"/v1/telegram-intake-drafts/{original['id']}/case-materials",
            headers=_upload_headers(actor, uuid4()), content=b"Synthetic anonymised note.",
        )
        assert uploaded.status_code == 201, uploaded.text
        workflow = _confirm(client, actor, data, UUID(original["id"]))
        before = _source_rows(workflow["case"]["id"])
        objects = deepcopy(store.objects)
        response = _request_draft(client, actor, workflow)
        assert response.status_code == 201, response.text
        draft = response.json()
        assert draft["draftData"]["incomingSourceStatus"] == "NOT_ATTACHED"
        assert draft["draftData"]["caseMaterialsStatus"] == "NOT_ATTACHED"
        materials = client.get(f"/v1/telegram-intake-drafts/{draft['id']}/case-materials",
                               headers=actor_headers(actor))
        assert materials.json() == {"items": []}
        assert _source_rows(workflow["case"]["id"]) == before
        assert store.objects == objects and store.deleted == []


@pytest.mark.parametrize("invalid_source", ["stale", "unconfirmed", "expired"])
def test_clarification_rejects_stale_unconfirmed_or_expired_sources(invalid_source: str) -> None:
    actor = 97_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    with application_client() as client:
        if invalid_source == "unconfirmed":
            case = client.post("/v1/cases", headers=actor_headers(actor, uuid4()),
                               json={"intakeSchemaVersion": "dental-case-intake.v2",
                                     "channel": "TELEGRAM"})
            assert case.status_code == 201, case.text
            case_id, source_hash = case.json()["id"], "0" * 64
        else:
            workflow = _confirm(client, actor, _guided_data())
            case_id, source_hash = workflow["case"]["id"], _source_hash(workflow)
        if invalid_source == "stale":
            source_hash = "0" * 64
        if invalid_source == "expired":
            engine = create_engine(owner_database_url())
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE cases SET retention_due_at=timezone('utc',now()) "
                    "- INTERVAL '1 minute' WHERE id=:id"
                ), {"id": case_id})
            engine.dispose()
        response = client.post(f"/v1/cases/{case_id}/clarification-drafts",
                               headers=actor_headers(actor, uuid4()),
                               json={"expectedFactSnapshotSha256": source_hash})
        assert response.status_code == (410 if invalid_source == "expired" else 409), response.text
        assert client.get("/v1/telegram-intake-drafts",
                          headers=actor_headers(actor)).json()["items"] == []


@pytest.mark.parametrize("other_actor", ["foreign", "same_clinic_noncreator"])
def test_clarification_does_not_grant_access_through_clinic_membership(other_actor: str) -> None:
    actor = 98_000_000_000 + uuid4().int % 100_000_000
    outsider = actor + 1_000_000_000
    clinic_id, _ = seed_admin(actor)
    if other_actor == "foreign":
        seed_admin(outsider)
    else:
        user_id, membership_id = uuid4(), uuid4()
        engine = create_engine(owner_database_url())
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users(id,telegram_user_id) VALUES(:id,:actor)"),
                               {"id": user_id, "actor": outsider})
            connection.execute(text(
                "INSERT INTO clinic_users(id,clinic_id,user_id,role) "
                "VALUES(:id,:clinic,:user,'CLINIC_ADMIN')"
            ), {"id": membership_id, "clinic": clinic_id, "user": user_id})
            connection.execute(text(
                "INSERT INTO subscription_entitlements(clinic_id,user_id,status,plan_code,"
                "starts_at) VALUES(:clinic,:user,'ACTIVE','MVP',timezone('utc',now()))"
            ), {"clinic": clinic_id, "user": user_id})
        engine.dispose()
    with application_client() as client:
        workflow = _confirm(client, actor, _guided_data())
        response = _request_draft(client, outsider, workflow)
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "CASE_NOT_FOUND"
        assert client.get("/v1/telegram-intake-drafts",
                          headers=actor_headers(outsider)).json()["items"] == []


@pytest.mark.parametrize("malformed", ["missing_key", "short_hash", "extra_field"])
def test_clarification_validates_its_closed_request_boundary(malformed: str) -> None:
    actor = 99_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    with application_client() as client:
        workflow = _confirm(client, actor, _guided_data())
        headers = actor_headers(actor, None if malformed == "missing_key" else uuid4())
        body = {"expectedFactSnapshotSha256": _source_hash(workflow)}
        if malformed == "short_hash":
            body["expectedFactSnapshotSha256"] = "a" * 63
        elif malformed == "extra_field":
            body["clinicId"] = str(uuid4())
        response = client.post(f"/v1/cases/{workflow['case']['id']}/clarification-drafts",
                               headers=headers, json=body)
        assert response.status_code == 422, response.text
        assert client.get("/v1/telegram-intake-drafts",
                          headers=actor_headers(actor)).json()["items"] == []
