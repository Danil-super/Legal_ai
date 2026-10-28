"""Existing ordinary/private-draft HTTP boundaries retain reference provenance."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_case_api import actor_headers
from test_lawyer_draft_context import read, routed_case as routed_case
from test_reference_evaluation_api import _add_member, _grant
from test_verified_copy_analysis_e2e import INTERNAL_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
SCENARIO = "Вымышленный пример: обращение клиники для проверки фактических уточнений."


def seed_reference(fixture):
    client, actor, clinic, member, *_ = fixture
    _grant(clinic, member, "CONTRIBUTOR")
    reviewer = 151_000_000_000 + uuid4().int % 100_000_000
    reviewer_member = _add_member(clinic, reviewer)
    _grant(clinic, reviewer_member, "REVIEWER")
    created = client.post("/v1/reference-evaluations", headers=actor_headers(actor, uuid4()),
                          json={"groupKey": "healthcare", "asOfDate": "2026-10-08",
                                "expectedRoute": "ABSTAIN", "scenarioText": SCENARIO})
    assert created.status_code == 201, created.text
    reference = created.json()["case"]["id"]
    assert client.post(f"/v1/reference-evaluations/{reference}/submit",
                       headers=actor_headers(actor, uuid4())).status_code == 200
    approved = client.post(f"/v1/reference-evaluations/{reference}/review",
                           headers=actor_headers(reviewer, uuid4()),
                           json={"decision": "APPROVE_FOR_EVALUATION"})
    assert approved.status_code == 200, approved.text
    return reference


@pytest.mark.parametrize("enabled", [False, True])
def test_draft_context_selection_is_opt_in_and_binds_trace_not_history_text(routed_case,
                                                                         monkeypatch, enabled):
    seed_reference(routed_case)
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1" if enabled else "0")
    response = read(routed_case)
    assert response.status_code == 200, response.text
    body = response.json()
    if enabled:
        examples = body["context"]["referenceExamples"]
        assert [item["scenarioText"] for item in examples] == [SCENARIO]
        assert body["source"]["context"]["referenceExampleTraceSha256"] == (
            body["context"]["referenceExampleTraceSha256"])
        assert SCENARIO not in str(body["source"])
        assert examples[0]["source"]["versionId"] not in {
            item["fragmentId"] for item in body["context"]["evidence"]}
    else:
        assert "referenceExamples" not in body["context"]
        assert "referenceExampleTraceSha256" not in body["source"]["context"]


def test_ordinary_context_and_submission_bind_example_trace_and_reject_retirement(routed_case,
                                                                                monkeypatch):
    reference = seed_reference(routed_case)
    monkeypatch.setenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "1")
    client, actor, _, _, case, *_ = routed_case
    with routed_case[9].begin() as connection:
        connection.execute(text("UPDATE cases SET status='ANALYSIS_BLOCKED' WHERE id=:id"),
                           {"id": case})
    headers = {**actor_headers(actor, uuid4()), "X-Agent-Internal-Key": INTERNAL_KEY}
    response = client.get(f"/v1/cases/{case}/analysis-context", headers=headers)
    assert response.status_code == 200, response.text
    context = response.json()
    assert [item["scenarioText"] for item in context["referenceExamples"]] == [SCENARIO]
    with routed_case[9].begin() as connection:
        connection.execute(text("UPDATE reference_evaluation_cases SET status='RETIRED' "
                                "WHERE id=:id"), {"id": reference})
    body = {"asOfDate": context["asOfDate"], "claims": [], "semanticReviews": [],
            "expectedFactSnapshotSha256": context["factSnapshotSha256"],
            "expectedEvidenceTraceSha256": context["evidenceTraceSha256"],
            "expectedClinicDocumentContextTraceSha256":
                context["clinicDocumentContextTraceSha256"],
            "expectedRiskPolicyVersion": context["riskPolicyVersion"],
            "expectedReferenceExampleTraceSha256": context["referenceExampleTraceSha256"]}
    denied = client.post(f"/v1/cases/{case}/analysis-submissions", headers=headers, json=body)
    assert denied.status_code == 409, denied.text
    assert denied.json()["error"]["code"] == "ANALYSIS_CONTEXT_STALE"
