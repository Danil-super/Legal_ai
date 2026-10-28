"""Money-request retrieval selects candidates, not a finding of refund entitlement."""

import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.contracts import FactKey
from legal_core.database import database_url, owner_database_url
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository
from legal_core.retrieval_plan import (
    is_semantic_safe_query,
    plan_legal_queries,
    retrieve_planned_evidence,
)
from legal_core.risk_engine import RiskPolicy, evaluate_risk
from test_case_api import actor_headers, application_client
from test_factual_safety_intake import screening
from test_legal_editor_workspace_api import _ingest, _version_payload, _write_manifest
from test_legal_editor_workspace_api import _seed_user
from test_verified_copy_analysis_e2e import EDITOR_KEY
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

CONSUMER_QUERY = "права потребителя недостатки оказанной услуги"
REFUND_QUERY = "возврат уплаченной суммы"


def facts(money="YES", amount=1_000_000, **change):
    value = ({"amountKopecks": amount, "currency": "RUB"} if money == "YES" else
             "NOT_REQUESTED" if money == "NO" else "UNKNOWN")
    return {FactKey.INTAKE_VERSION: "GUIDED_V2", FactKey.SITUATION_AREAS: ["TREATMENT"],
            FactKey.INCOMING_COMMUNICATION: "COMPLAINT",
            FactKey.HEALTH_CONSEQUENCE_SIGNALS: ["NO_KNOWN_INFORMATION"],
            FactKey.CLINIC_ACTIONS: ["INVITED_FOR_EXAMINATION"],
            FactKey.FACTUAL_SAFETY_SCREENING: screening(moneyRequested=money, amount=value),
            **change}


@pytest.mark.parametrize("amount,risk", [(500_000, "MEDIUM"), (1_000_000, "HIGH"),
                                       (1_000_001, "HIGH")])
def test_confirmed_money_request_searches_remedies_before_clinic_offers_them(amount, risk):
    data = facts(amount=amount)
    queries = plan_legal_queries(data)
    assert {CONSUMER_QUERY, REFUND_QUERY, "безвозмездное устранение недостатков",
            "повторное выполнение работы"} <= set(queries)
    assert len(queries) == len(set(queries)) <= 20
    assert all(is_semantic_safe_query(item) for item in queries)
    assert queries == plan_legal_queries(facts(amount=500_000))
    policy = RiskPolicy(version="dental-risk.v4", high_demand_threshold_kopecks=1_000_000,
                        guided_v2_explicit_signals_enabled=True, factual_safety_intake_enabled=True)
    assert evaluate_risk(data, policy=policy, evidence_verified=True).level == risk


@pytest.mark.parametrize("area", ["TREATMENT", "SERVICE"])
def test_reported_money_request_can_select_candidates_with_unknown_amount_without_inference(area):
    data = facts()
    data[FactKey.SITUATION_AREAS] = [area]
    data[FactKey.FACTUAL_SAFETY_SCREENING]["amount"] = "UNKNOWN"
    queries = plan_legal_queries(data)
    assert CONSUMER_QUERY in queries and REFUND_QUERY in queries
    assert data[FactKey.FACTUAL_SAFETY_SCREENING]["amount"] == "UNKNOWN"


@pytest.mark.parametrize("fault", ["NO", "UNKNOWN", "missing", "malformed", "narrative-only",
                                   "legacy-demand-only", "privacy-only", "no-area"])
def test_new_expansion_requires_valid_positive_scoped_facts(fault):
    data = facts(money=fault if fault in {"NO", "UNKNOWN"} else "YES")
    if fault in {"missing", "narrative-only", "legacy-demand-only"}:
        data.pop(FactKey.FACTUAL_SAFETY_SCREENING)
    if fault == "narrative-only":
        data[FactKey.EVENT_SUMMARY] = "SYNTHETIC_PRIVATE_CANARY: вернуть деньги за пломбу."
    if fault == "legacy-demand-only":
        data[FactKey.PATIENT_DEMAND] = ["REFUND_DEMAND"]
    if fault == "malformed":
        data[FactKey.FACTUAL_SAFETY_SCREENING]["moneyRequested"] = True
    if fault == "privacy-only":
        data[FactKey.SITUATION_AREAS] = ["PERSONAL_DATA"]
    if fault == "no-area":
        data.pop(FactKey.SITUATION_AREAS)
    queries = plan_legal_queries(data)
    assert CONSUMER_QUERY not in queries and REFUND_QUERY not in queries
    assert all("SYNTHETIC_PRIVATE_CANARY" not in item for item in queries)


def test_dense_case_stays_bounded_deduplicated_and_preserves_existing_clinic_action_queries():
    data = facts()
    data[FactKey.SITUATION_AREAS] = ["TREATMENT", "SERVICE", "PERSONAL_DATA", "MEDICAL_RECORDS"]
    data[FactKey.CLINIC_ACTIONS] = ["OFFERED_REFUND", "OFFERED_CORRECTION"]
    data[FactKey.HEALTH_CONSEQUENCE_SIGNALS] = ["HOSPITALIZATION"]
    queries = plan_legal_queries(data)
    assert len(queries) == len(set(queries)) <= 20
    assert queries.count("безвозмездное устранение недостатков") == 1
    assert all(is_semantic_safe_query(item) for item in queries)
    for money in ("NO", "UNKNOWN"):
        unchanged = facts(money=money)
        unchanged[FactKey.CLINIC_ACTIONS] = ["OFFERED_REFUND", "OFFERED_CORRECTION"]
        old_queries = plan_legal_queries(unchanged)
        assert "возврат денежных средств медицинские услуги" in old_queries
        assert "безвозмездное устранение недостатков" in old_queries


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                    reason="requires disposable PostgreSQL")
def test_generic_consumer_fragment_survives_medical_noise_with_approval_and_date_gates(
    isolated_database, tmp_path,
):
    # Deliberately no medical lexeme and no exact full query substring. The real
    # Russian FTS must find normalized terms across ordinary legal prose.
    generic = ("Синтетическая учебная норма. При обнаружении недостатков работы потребитель "
               "имеет права, связанные с качеством оказанной услуги. Возврат суммы, ранее "
               "уплаченной потребителем, рассматривается при установленных условиях.")
    noise = ("Синтетический профессиональный стандарт. Платные медицинские услуги. Права "
             "пациента при оказании медицинской помощи. Ответственность исполнителя "
             "медицинских услуг. Нормы налогового учёта.")
    _seed_user(103_900_000_001, system_role="LEGAL_EDITOR")
    specs = [(generic, True, "2026-10-08", "2027-01-01"),
             (generic, False, "2026-01-01", "2027-01-01"),
             (generic, True, "2026-01-01", "2026-09-01")]
    specs.extend((noise + f" Учебный раздел {index}.", True, "2026-01-01", "2027-01-01")
                 for index in range(5))
    versions = []
    with application_client() as client:
        for value, approved, start, end in specs:
            version = _ingest(_write_manifest(tmp_path, fragment_text=value,
                                               effective_from=start, effective_to=end))
            versions.append(version)
            if approved:
                response = client.post(f"/v1/legal/review-queue/{version}/approval-events",
                    headers={**actor_headers(103_900_000_001, uuid4()),
                             "X-Legal-Editor-Gateway-Key": EDITOR_KEY},
                    json=_version_payload(version))
                assert response.status_code == 200, response.text

    async def retrieve(as_of):
        engine = create_async_engine(database_url())
        try:
            async with async_sessionmaker(engine)() as session:
                return await retrieve_planned_evidence(ApprovedLegalCorpusRepository(session),
                    queries=plan_legal_queries(facts()), as_of_date=as_of)
        finally:
            await engine.dispose()

    current = asyncio.run(retrieve(date(2026, 10, 27)))
    assert versions[0] in {item.version_id for item in current}
    assert versions[1] not in {item.version_id for item in current}  # pending
    assert versions[2] not in {item.version_id for item in current}  # expired
    assert len(current) <= 20
    historical = asyncio.run(retrieve(date(2026, 8, 20)))
    assert versions[2] in {item.version_id for item in historical}
    assert versions[0] not in {item.version_id for item in historical}
    owner = create_engine(owner_database_url())
    try:
        with owner.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM legal_approval_events")) == 7
    finally:
        owner.dispose()
