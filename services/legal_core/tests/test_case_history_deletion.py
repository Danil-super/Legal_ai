"""Deletion closes every content path and uses the existing storage-first purge."""

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from legal_core.case_api import _canonical_hash
from legal_core.case_material_retention import purge_expired_case_materials
from legal_core.case_retention import purge_expired_case_content
from legal_core.contracts import CaseStatus
from legal_core.database import database_url, owner_database_url
from legal_core.models import AuditEvent, Case, CaseMaterial, CaseReport, TelegramCaseWorkflow
from legal_core.reports import build_intake_report
from test_case_api import actor_headers, application_client, seed_admin
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("role", ["CLINIC_OWNER", "CLINIC_ADMIN", "CLINIC_LAWYER"])
def test_delete_own_case_closes_reports_and_materials_and_is_idempotent(isolated_database, role):
    actor = 132_000_000_000 + uuid4().int % 100_000_000
    clinic, member = seed_admin(actor, role=role)
    engine = create_engine(owner_database_url())
    now = datetime.now(UTC)
    pdf = b"%PDF-1.7\nsynthetic deletion test"
    with Session(engine) as db, db.begin():
        case = Case(clinic_id=clinic, created_by_membership_id=member,
                    status="ANALYSIS_BLOCKED", retention_due_at=now + timedelta(days=30))
        untouched = Case(clinic_id=clinic, created_by_membership_id=member,
                         retention_due_at=now + timedelta(days=30))
        db.add_all([case, untouched])
        db.flush()
        case_id, other_id, report_id = case.id, untouched.id, uuid4()
        canonical = build_intake_report(
            report_id=report_id, case_id=case_id, public_number="DL-2026-000001",
            case_status=CaseStatus.ANALYSIS_BLOCKED, report_version=1,
            generated_at=now, facts={}, missing_facts=[],
        )
        wire = canonical.model_dump(mode="json", by_alias=True)
        db.add(CaseReport(id=report_id, clinic_id=clinic, case_id=case_id, report_version=1,
                          schema_version=canonical.schema_version, status="ANALYSIS_BLOCKED",
                          report_json=wire, content_sha256=_canonical_hash(wire), pdf_bytes=pdf,
                          pdf_sha256=hashlib.sha256(pdf).hexdigest(), pdf_size_bytes=len(pdf),
                          facts_snapshot_sha256=canonical.fact_snapshot_sha256,
                          created_by_membership_id=member))
        db.add(CaseMaterial(clinic_id=clinic, case_id=case_id, uploader_membership_id=member,
                            display_name="Материал 1", mime_type="text/plain", raw_size_bytes=9,
                            raw_sha256="a" * 64, raw_object_key="synthetic/" + uuid4().hex,
                            expires_at=now + timedelta(days=30)))
    with application_client() as client:
        path = f"/v1/case-history/{case_id}"
        assert client.get(f"/v1/reports/{report_id}/pdf",
                          headers=actor_headers(actor)).content == pdf
        first = client.delete(path, headers=actor_headers(actor))
        assert first.status_code == 200, first.text
        assert first.json() == {"caseId": str(case_id), "status": "DELETION_SCHEDULED"}
        assert client.delete(path, headers=actor_headers(actor)).json() == first.json()
        assert str(case_id) not in client.get("/v1/case-history",
                                             headers=actor_headers(actor)).text
        assert client.get(path, headers=actor_headers(actor)).status_code == 410
        assert client.get(f"/v1/cases/{case_id}", headers=actor_headers(actor)).status_code == 410
        assert client.get(f"/v1/reports/{report_id}/pdf",
                          headers=actor_headers(actor)).status_code == 410
        assert client.get(f"/v1/cases/{case_id}/case-materials",
                          headers=actor_headers(actor)).status_code == 410
        assert client.post(f"/v1/cases/{case_id}/analysis-jobs", json={"messageId": 1},
                           headers=actor_headers(actor)).status_code == 410
        assert client.get(f"/v1/case-history/{other_id}",
                          headers=actor_headers(actor)).status_code == 200
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.resource_id == case_id, AuditEvent.action == "CASE_DELETION_REQUESTED",
        )) == 1
        assert db.scalar(select(CaseMaterial.expires_at).where(
            CaseMaterial.case_id == case_id,
        )) <= datetime.now(UTC)

    class Store:
        def __init__(self):
            self.deleted: list[str] = []

        async def delete_case_material(self, *, stored_location: str) -> None:
            self.deleted.append(stored_location)

    async def cleanup():
        runtime = create_async_engine(database_url())
        sessions = async_sessionmaker(runtime, expire_on_commit=False)
        try:
            assert await purge_expired_case_content(sessions) == 0  # Raw object still exists.
            store = Store()
            assert await purge_expired_case_materials(sessions, store) == 1
            assert len(store.deleted) == 1
            assert await purge_expired_case_content(sessions) == 1
        finally:
            await runtime.dispose()

    asyncio.run(cleanup())
    with Session(engine) as db:
        assert db.get(Case, case_id).content_purged_at is not None
        assert db.get(CaseReport, report_id) is None
    engine.dispose()


def test_delete_expires_origin_and_correction_drafts_without_erasing_replay_tombstone(
    isolated_database,
):
    actor = 134_000_000_000 + uuid4().int % 100_000_000
    clinic, member = seed_admin(actor)
    engine = create_engine(owner_database_url())
    with application_client() as client:
        origin = client.post("/v1/telegram-intake-drafts", json={},
                             headers=actor_headers(actor, uuid4())).json()
        update_key = uuid4()
        payload = {"expectedRevision": origin["revision"], "wizardState": "SERVICE_TYPE",
                   "draftData": {"problem_summary": "Вымышленный учебный кейс"}}
        updated = client.put(f"/v1/telegram-intake-drafts/{origin['id']}", json=payload,
                             headers=actor_headers(actor, update_key))
        assert updated.status_code == 200, updated.text
        with Session(engine) as db, db.begin():
            case = Case(clinic_id=clinic, created_by_membership_id=member)
            db.add(case)
            db.flush()
            case_id = case.id
            db.add(TelegramCaseWorkflow(id=origin["id"], clinic_id=clinic,
                                        actor_membership_id=member, request_sha256="a" * 64,
                                        state="SUCCEEDED", case_id=case_id, receipt_version=2))
        correction = client.post("/v1/telegram-intake-drafts", json={},
                                 headers=actor_headers(actor, uuid4())).json()
        # Simulate a source-bound correction draft already prepared before deletion.
        with engine.begin() as db:
            db.execute(text("UPDATE telegram_intake_drafts SET draft_json=jsonb_build_object("
                            "'correctionSource',jsonb_build_object('caseId',CAST(:case AS text))) "
                            "WHERE id=:id"),
                       {"case": str(case_id), "id": correction["id"]})
        assert client.delete(f"/v1/case-history/{case_id}",
                             headers=actor_headers(actor)).status_code == 200
        replay = client.put(f"/v1/telegram-intake-drafts/{origin['id']}", json=payload,
                            headers=actor_headers(actor, update_key))
        assert replay.status_code == 410 and "draftData" not in replay.text
        for draft_id in (origin["id"], correction["id"]):
            assert client.get(f"/v1/telegram-intake-drafts/{draft_id}",
                              headers=actor_headers(actor)).status_code == 404
        with engine.connect() as db:
            row = db.execute(text("SELECT response_json,resource_id FROM idempotency_records "
                                  "WHERE key=:key"), {"key": str(update_key)}).one()
            assert row.response_json is None and str(row.resource_id) == origin["id"]
    engine.dispose()


def test_deletion_is_creator_and_tenant_scoped_and_revocation_is_rechecked(isolated_database):
    actor = 133_000_000_000 + uuid4().int % 100_000_000
    clinic, member = seed_admin(actor)
    foreign_clinic, foreign_member = seed_admin(actor + 1)
    _, colleague_member = seed_admin(actor + 2, entitlement_status=None)
    engine = create_engine(owner_database_url())
    with engine.begin() as db:
        db.execute(text("UPDATE clinic_users SET clinic_id=:clinic WHERE id=:id"),
                   {"clinic": clinic, "id": colleague_member})
    with Session(engine) as db, db.begin():
        own = Case(clinic_id=clinic, created_by_membership_id=member)
        foreign = Case(clinic_id=foreign_clinic, created_by_membership_id=foreign_member)
        colleague = Case(clinic_id=clinic, created_by_membership_id=colleague_member)
        db.add_all([own, foreign, colleague])
        db.flush()
        own_id, foreign_id, colleague_id = own.id, foreign.id, colleague.id
    with application_client() as client:
        assert client.delete(f"/v1/case-history/{foreign_id}",
                             headers=actor_headers(actor)).status_code == 404
        assert client.delete(f"/v1/case-history/{colleague_id}",
                             headers=actor_headers(actor)).status_code == 404
        with engine.begin() as db:
            db.execute(text("UPDATE clinic_users SET status='REVOKED' WHERE id=:id"),
                       {"id": member})
        assert client.delete(f"/v1/case-history/{own_id}",
                             headers=actor_headers(actor)).status_code == 403
    with Session(engine) as db:
        assert db.get(Case, own_id).retention_due_at is None
        assert db.get(Case, foreign_id).retention_due_at is None
        assert db.get(Case, colleague_id).retention_due_at is None
    engine.dispose()
