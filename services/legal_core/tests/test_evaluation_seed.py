"""Synthetic domain regressions, explicitly not human-approved legal answer gold data."""

import asyncio
import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
from legal_core.analysis_api import _analysis_date
from legal_core.case_api import ApiError
from legal_core.contracts import FactKey
from legal_core.intake import missing_facts_for
from legal_core.retrieval_plan import (
    is_semantic_safe_query, plan_legal_queries, retrieve_planned_evidence,
)
from legal_core.risk_engine import RiskPolicy, evaluate_early_triage, evaluate_risk
from legal_core.safe_patient_draft import build_safe_patient_draft

PACK = json.loads((Path(__file__).resolve().parents[1] /
                   "fixtures/evaluation/scenarios.v1.json").read_text())
CASES = [PACK["defaults"] | row for row in PACK["cases"]]
POLICY = RiskPolicy(
    version=PACK["policy"]["version"],
    high_demand_threshold_kopecks=PACK["policy"]["highDemandThresholdKopecks"],
)


def test_seed_is_unique_and_never_claims_human_approval():
    assert PACK["schemaVersion"] == "dental-evaluation-seed.v1"
    assert PACK["purpose"] == "TECHNICAL_REGRESSION_NOT_LEGAL_GOLD"
    assert PACK["legalReviewStatus"] == "PENDING_HUMAN_REVIEW"
    assert len(CASES) == 40
    assert len({row["id"] for row in CASES}) == len(CASES)
    assert all(row["title"] for row in CASES)


@pytest.mark.parametrize("scenario", CASES, ids=[row["id"] for row in CASES])
def test_synthetic_domain_scenario(scenario):
    raw = deepcopy(PACK["baseFacts"])
    raw.update(deepcopy(scenario["factsDelta"]))
    facts = {FactKey(key): value for key, value in raw.items()}
    before = deepcopy(facts)
    assert missing_facts_for(facts) == []
    queries = plan_legal_queries(facts)
    assert set(scenario["expectedQueries"]) <= set(queries)
    assert len(queries) == len(set(queries)) <= 20
    for query in queries:
        if query.startswith("медицинская услуга "):
            assert not is_semantic_safe_query(query)
            assert len(query) <= len("медицинская услуга ") + 120
        else:
            assert is_semantic_safe_query(query)
            assert "SYNTHETIC_CANARY" not in query
    early = evaluate_early_triage(facts, policy=POLICY)
    assert (early.level.value if early is not None else None) == scenario["expectedEarlyTriage"]
    # This tests an isolated deterministic function, not permission to publish an analysis.
    risk = evaluate_risk(facts, policy=POLICY, evidence_verified=scenario["evidenceVerified"])
    assert risk.level.value == scenario["expectedDomainRisk"]
    assert risk.external_draft_allowed is (risk.level.value == "LOW")
    assert facts == before
    if scenario["expectedDateError"] is not None:
        with pytest.raises(ApiError) as raised:
            _analysis_date(facts)
        assert raised.value.code == scenario["expectedDateError"]
        # A LOW domain result cannot override the earlier exact-date publication gate.
        return
    assert _analysis_date(facts) == date.fromisoformat(scenario["expectedAnalysisDate"])
    draft = build_safe_patient_draft(facts, risk)
    assert draft.status == ("AVAILABLE" if risk.level.value == "LOW" else "BLOCKED")
    assert draft.human_approval_required


def test_combined_query_plan_is_bounded_and_has_scenario_coverage():
    queries = plan_legal_queries({
        FactKey.INCIDENT_TYPES: ["QUALITY_COMPLAINT", "PERSONAL_DATA", "INFORMED_CONSENT"],
        FactKey.PATIENT_DEMAND: ["REFUND_DEMAND", "COMPENSATION_DEMAND", "REWORK_DEMAND",
                               "DOCUMENT_REQUEST"],
        FactKey.FORMAL_CLAIM: "YES", FactKey.HARM_CLAIMED: "YES",
        FactKey.REGULATOR_OR_COURT: "YES", FactKey.REGULATOR_THREAT: "YES",
        FactKey.SERVICE_TYPE: "SYNTHETIC_CANARY " * 100,
    })
    assert len(queries) <= 20
    assert {"гарантийный срок", "врачебная тайна", "информированное добровольное согласие",
            "безвозмездное устранение недостатков"} <= set(queries)
    assert all("SYNTHETIC_CANARY" not in q for q in queries if is_semantic_safe_query(q))


def test_scenario_retrieval_preserves_date_budget_and_external_privacy():
    calls = []

    class Repository:
        async def search(self, query, **kwargs):
            calls.append((query, kwargs))
            return []

    facts = {FactKey.PRIMARY_INCIDENT_TYPE: "PERSONAL_DATA",
             FactKey.SERVICE_TYPE: "SYNTHETIC_CANARY internal note"}
    queries = plan_legal_queries(facts)
    target_date = date(2026, 9, 1)
    result = asyncio.run(retrieve_planned_evidence(
        Repository(), queries=queries, as_of_date=target_date,
    ))
    assert result == []
    assert len(calls) == len(queries)
    for query, options in calls:
        assert options["as_of_date"] == target_date
        assert options["limit"] == 5
        assert options["semantic"] == is_semantic_safe_query(query)
