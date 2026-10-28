"""Natural source expiry during an actual late risk-policy FK wait rolls back all results."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_case_api import actor_headers
from test_factual_safety_intake import screening
from test_lawyer_draft_context import routed_case as routed_case
from test_reference_analogy_context import seed_reference
from test_report_free_workflow import _payload
from test_verified_copy_analysis_e2e import INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("branch", ["legacy-report", "v2-limited", "v2-ready"])
def test_final_source_fence_after_late_fk_wait_rolls_back_every_commit_branch(routed_case,
                                                                           monkeypatch, branch):
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    reference = seed_reference(routed_case)
    client, actor, *_ = routed_case
    # A separate normal 5000 RUB case under the unchanged current v4 policy.
    created = client.post(f"/v1/telegram-case-workflows/{uuid4()}/submissions",
        headers=actor_headers(actor), json=_payload(safetyScreening=screening(
            moneyRequested="YES", amount={"amountKopecks": 500_000, "currency": "RUB"})))
    assert created.status_code == 201, created.text
    case = created.json()["case"]["id"]
    key = uuid4()
    headers = {**actor_headers(actor, key), "X-Agent-Internal-Key": INTERNAL_KEY}
    response = client.get(f"/v1/cases/{case}/analysis-context", headers=headers)
    assert response.status_code == 200, response.text
    context = response.json()
    assert context.get("referenceExamples")
    body = {"asOfDate": context["asOfDate"], "claims": [], "semanticReviews": [],
            "expectedFactSnapshotSha256": context["factSnapshotSha256"],
            "expectedEvidenceTraceSha256": context["evidenceTraceSha256"],
            "expectedClinicDocumentContextTraceSha256":
                context["clinicDocumentContextTraceSha256"],
            "expectedRiskPolicyVersion": context["riskPolicyVersion"],
            "expectedReferenceExampleTraceSha256": context["referenceExampleTraceSha256"]}
    if branch == "v2-ready":
        fragment = context["evidence"][0]["fragmentId"]
        body.update(claims=[{"claimId": "record", "kind": "ACTION",
            "text": "Зафиксировать обращение.", "evidenceFragmentIds": [fragment],
            "requiredFactKeys": ["EVENT_SUMMARY"]}], semanticReviews=[{
                "claimId": "record", "verdict": "SUPPORTED", "reviewedFragmentIds": [fragment]}])
    endpoint = "analysis-submissions" if branch == "legacy-report" else "preparation-submissions"
    owner = routed_case[9]
    with owner.begin() as connection:
        connection.execute(text("UPDATE reference_evaluation_case_versions SET "
            "content_expires_at=clock_timestamp()+interval '2 seconds' "
            "WHERE reference_case_id=:id"), {"id": reference})
    with owner.connect() as connection, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = connection.begin()
        connection.execute(text("SELECT id FROM risk_policy_versions "
                                "WHERE status='APPROVED' FOR UPDATE"))
        future = pool.submit(client.post, f"/v1/cases/{case}/{endpoint}",
                             headers=headers, json=body)
        deadline = time.monotonic() + 5
        blocked = False
        while time.monotonic() < deadline:
            with owner.connect() as observer:
                blocked = bool(observer.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE "
                    "datname=current_database() AND wait_event_type='Lock' "
                    "AND query LIKE '%case_risk_assessments%'")))
            if blocked:
                break
            time.sleep(0.02)
        time.sleep(2.1)
        transaction.commit()
        result = future.result(timeout=5)
        assert blocked, result.text
        assert result.status_code == 409, result.text
        assert result.json()["error"]["code"] == "REFERENCE_ANALOGY_CONTEXT_STALE"
    with owner.connect() as connection:
        for table in ("case_risk_assessments", "case_analysis_runs", "case_reports"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE case_id=:id"),
                                     {"id": case}) == 0
        assert connection.scalar(text("SELECT count(*) FROM idempotency_records WHERE key=:key "
                                      "AND scope LIKE :scope"),
                                 {"key": str(key), "scope": f"%{case}%"}) == 0
        assert connection.scalar(text("SELECT status FROM cases WHERE id=:id"),
                                 {"id": case}) == "ANALYSIS_BLOCKED"
