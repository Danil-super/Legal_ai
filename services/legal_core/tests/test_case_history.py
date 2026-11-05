"""Actual tenant/creator boundaries and metadata-only pagination for saved cases."""

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from legal_core.case_api import _canonical_hash
from legal_core.contracts import CaseStatus
from legal_core.database import owner_database_url
from legal_core.models import Case, CaseReport
from legal_core.reports import build_intake_report
from test_case_api import actor_headers, application_client, seed_admin
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("role", ["CLINIC_OWNER", "CLINIC_ADMIN", "CLINIC_LAWYER"])
def test_history_only_lists_own_live_cases_and_never_case_text(isolated_database, role):
    actor = 130_000_000_000 + uuid4().int % 100_000_000
    clinic, member = seed_admin(actor, role=role)
    other_clinic, other_member = seed_admin(actor + 1)
    engine = create_engine(owner_database_url())
    with Session(engine) as db, db.begin():
        cases = [Case(clinic_id=clinic, created_by_membership_id=member,
                      title="SYNTHETIC_PRIVATE_CANARY", status="INTAKE_COMPLETE",
                      retention_due_at=datetime.now(UTC) + timedelta(days=30)) for _ in range(9)]
        db.add_all(cases)
        expired = Case(clinic_id=clinic, created_by_membership_id=member,
                       retention_due_at=datetime.now(UTC) - timedelta(seconds=1))
        foreign = Case(clinic_id=other_clinic, created_by_membership_id=other_member)
        db.add_all([expired, foreign])
        db.flush()
        expected = {str(case.id) for case in cases}
        foreign_id, expired_id = str(foreign.id), str(expired.id)
    with application_client() as client:
        first = client.get("/v1/case-history", headers=actor_headers(actor))
        assert first.status_code == 200, first.text
        assert len(first.json()["items"]) == 8 and first.json()["nextOffset"] == 8
        second = client.get("/v1/case-history?offset=8", headers=actor_headers(actor))
        assert len(second.json()["items"]) == 1 and second.json()["nextOffset"] is None
        assert {case["id"] for case in first.json()["items"] + second.json()["items"]} == expected
        assert "SYNTHETIC_PRIVATE_CANARY" not in first.text + second.text
        assert client.get(f"/v1/case-history/{foreign_id}",
                          headers=actor_headers(actor)).status_code == 404
        assert client.get(f"/v1/case-history/{expired_id}",
                          headers=actor_headers(actor)).status_code == 410
        for offset in (-1, 100001):
            assert client.get(f"/v1/case-history?offset={offset}",
                              headers=actor_headers(actor)).status_code == 422
    engine.dispose()


def test_report_history_and_download_stay_owned_and_reauthorize(isolated_database):
    actor = 131_000_000_000 + uuid4().int % 100_000_000
    clinic, member = seed_admin(actor)
    _, other_member = seed_admin(actor + 1, entitlement_status=None)
    engine = create_engine(owner_database_url())
    with Session(engine) as db, db.begin():
        case = Case(clinic_id=clinic, created_by_membership_id=member, status="ANALYSIS_BLOCKED")
        db.add(case)
        db.flush()
        case_id = case.id
        pdf = b"%PDF-1.7\nsynthetic"
        reports = []
        for version in range(1, 10):
            rid = uuid4()
            canonical = build_intake_report(
                report_id=rid, case_id=case.id, public_number="DL-2026-000001",
                case_status=CaseStatus.ANALYSIS_BLOCKED, report_version=version,
                generated_at=datetime.now(UTC), facts={}, missing_facts=[],
            )
            wire = canonical.model_dump(mode="json", by_alias=True)
            db.add(CaseReport(id=rid, clinic_id=clinic, case_id=case.id, report_version=version,
                             schema_version=canonical.schema_version, status="ANALYSIS_BLOCKED",
                             report_json=wire, content_sha256=_canonical_hash(wire), pdf_bytes=pdf,
                             pdf_sha256=hashlib.sha256(pdf).hexdigest(), pdf_size_bytes=len(pdf),
                             facts_snapshot_sha256=canonical.fact_snapshot_sha256,
                             created_by_membership_id=member))
            reports.append(str(rid))
    with application_client() as client:
        path = f"/v1/case-history/{case_id}"
        first = client.get(path, headers=actor_headers(actor))
        assert first.status_code == 200, first.text
        assert [x["reportVersion"] for x in first.json()["reports"]] == list(range(9, 1, -1))
        assert all(x["kind"] == "CARD" for x in first.json()["reports"])
        assert "reportJson" not in first.text and "pdf_bytes" not in first.text
        assert client.get(path + "?offset=8", headers=actor_headers(actor)).json()["reports"][0][
            "reportVersion"] == 1
        assert client.get(f"/v1/reports/{reports[0]}/pdf",
                          headers=actor_headers(actor)).content == pdf
        # Same-tenant colleague is not the creator; the new history is personal even for owners.
        with engine.begin() as db:
            db.execute(text("UPDATE clinic_users SET clinic_id=:clinic,role='CLINIC_OWNER' "
                            "WHERE id=:id"), {"clinic": clinic, "id": other_member})
            db.execute(text("INSERT INTO subscription_entitlements "
                            "(clinic_id,user_id,status,plan_code,starts_at) "
                            "SELECT :clinic,user_id,'ACTIVE','MVP',"
                            "clock_timestamp()-interval '1 day' "
                            "FROM clinic_users WHERE id=:id"),
                       {"clinic": clinic, "id": other_member})
        assert client.get(path, headers=actor_headers(actor + 1)).status_code == 404
        colleague = client.get("/v1/case-history", headers=actor_headers(actor + 1))
        assert colleague.json()["items"] == []
        with engine.begin() as db:
            db.execute(text("UPDATE clinic_users SET status='REVOKED' WHERE id=:id"),
                       {"id": member})
        assert client.get(path, headers=actor_headers(actor)).status_code == 403
        assert client.get(f"/v1/reports/{reports[0]}/pdf",
                          headers=actor_headers(actor)).status_code == 403
    engine.dispose()
