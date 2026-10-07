"""Disposable PostgreSQL proof of the direct human-approved transition and routing."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.case_api import _domain_facts
from legal_core.database import database_url, owner_database_url
from legal_core.models import CaseEscalation, CaseFact, CaseRiskAssessment, RiskPolicyVersion, User
from legal_core.risk_engine import RiskPolicy, evaluate_early_triage, evaluate_risk
from legal_core.risk_persistence import record_case_risk_assessment
from legal_core.risk_policy_approval import RiskPolicyApproval, approve_risk_policy
from test_case_api import actor_headers, application_client, seed_admin
from telegram_gateway.case_wizard import facts_from_v2_data

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="requires disposable PostgreSQL",
)


def approval(reviewer: int, *, guided: bool, threshold: int = 5_000_000) -> RiskPolicyApproval:
    return RiskPolicyApproval(
        reviewer_telegram_user_id=reviewer,
        version=3 if guided else 1,
        high_demand_threshold_kopecks=threshold,
        incident_triggers_reviewed=True,
        monetary_threshold_reviewed=True,
        escalation_rules_reviewed=True,
        early_triage_enabled=guided,
        guided_v2_explicit_signals_enabled=guided,
        supersede_approved=guided,
        direct_v1_supersession_reviewed=guided,
    )


def reset_policies() -> None:
    # Each test uses an isolated disposable database. Existing immutable guards
    # forbid normal DELETE; owner TRUNCATE is confined to the disposable test DB.
    assert os.environ["POSTGRES_DB"].startswith("dental_legal_test_")
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE risk_policy_versions CASCADE"))
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def isolate_policy_state():
    reset_policies()
    yield
    reset_policies()


def prepare_policy(*, initial_threshold: int = 5_000_000, activate: bool = True):
    async def run():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        reviewer = 8_310_000_000 + uuid4().int % 1_000_000_000
        try:
            async with factory() as session, session.begin():
                session.add(User(telegram_user_id=reviewer, system_role="LEGAL_EDITOR"))
            v1 = await approve_risk_policy(
                factory, approval(reviewer, guided=False, threshold=initial_threshold)
            )
            v3 = (
                await approve_risk_policy(factory, approval(reviewer, guided=True))
                if activate
                else None
            )
            return reviewer, v1, v3
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_direct_v1_to_v3_is_atomic_human_only_idempotent_and_preserves_v1():
    reviewer, v1_id, v3_id = prepare_policy()

    async def run():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            assert await approve_risk_policy(factory, approval(reviewer, guided=True)) == v3_id
            async with factory() as session:
                rows = (
                    await session.scalars(
                        select(RiskPolicyVersion)
                        .where(RiskPolicyVersion.policy_key == "dental-risk")
                        .order_by(RiskPolicyVersion.version)
                    )
                ).all()
                assert [(row.version, row.status) for row in rows] == [
                    (1, "RETIRED"),
                    (3, "APPROVED"),
                ]
                assert rows[0].id == v1_id
                assert rows[0].policy_json == {
                    "schemaVersion": "risk-policy.v1",
                    "highDemandThresholdKopecks": 5_000_000,
                }
                events = (
                    await session.execute(
                        text("SELECT decision, count(*) FROM risk_policy_events GROUP BY decision")
                    )
                ).all()
                assert dict(events) == {"APPROVED": 2, "RETIRED": 1}
            ordinary = 8_420_000_000 + uuid4().int % 1_000_000_000
            async with factory() as session, session.begin():
                session.add(User(telegram_user_id=ordinary))
            with pytest.raises(PermissionError, match="LEGAL_EDITOR"):
                await approve_risk_policy(factory, approval(ordinary, guided=True))
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_changed_predecessor_rolls_back_without_creating_or_approving_v3():
    reviewer, _, _ = prepare_policy(initial_threshold=5_000_001, activate=False)

    async def run():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            with pytest.raises(ValueError, match="exact approved v1 predecessor"):
                await approve_risk_policy(factory, approval(reviewer, guided=True))
            async with factory() as session:
                rows = (await session.scalars(select(RiskPolicyVersion))).all()
                assert [(row.version, row.status) for row in rows] == [(1, "APPROVED")]
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("entrypoint", ["workflow", "finalization"])
@pytest.mark.parametrize("safe_stop", [False, True])
@pytest.mark.parametrize(
    ("health", "incoming", "level"),
    [
        (["HOSPITALIZATION"], "COMPLAINT", "CRITICAL"),
        (["UNKNOWN"], "AUTHORITY_OR_COURT_DOCUMENT", "CRITICAL"),
        (["NO_KNOWN_INFORMATION"], "COMPLAINT", "HIGH"),
    ],
)
def test_both_confirmations_route_v3_without_corpus_or_llm_and_replay_one_escalation(
    entrypoint,
    health,
    incoming,
    level,
    safe_stop,
    monkeypatch,
):
    _, _, policy_id = prepare_policy()
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1" if safe_stop else "0")
    user = 8_510_000_000 + uuid4().int % 1_000_000_000
    other = 8_610_000_000 + uuid4().int % 1_000_000_000
    clinic_id, membership_id = seed_admin(user)
    seed_admin(other)
    facts = facts_from_v2_data(
        {
            "intakeVersion": 2,
            "incomingKind": incoming,
            "incomingSourceStatus": "NOT_ATTACHED",
            "situationAreas": ["SERVICE"],
            "eventSummary": "Вымышленная ситуация для проверки маршрутизации.",
            "eventDate": {"date": "2026-09-01", "precision": "EXACT"},
            "conflictStage": "FIRST",
            "clinicActions": ["NOTHING_YET"],
            "healthSignals": health,
            "caseMaterialsStatus": "NOT_ATTACHED",
        }
    )
    if level == "HIGH":
        facts.append(
            {
                "factKey": "DEMAND_AMOUNT",
                "valueType": "MONEY",
                "value": {"amountKopecks": 5_000_000, "currency": "RUB"},
                "sourceType": "USER_STATEMENT",
            }
        )
    with application_client() as client:
        if entrypoint == "workflow":
            path = f"/v1/telegram-case-workflows/{uuid4()}/submissions"
            payload = {
                "intakeSchemaVersion": "dental-case-intake.v2",
                "locale": "ru-RU",
                "facts": facts,
            }
            first = client.post(path, headers=actor_headers(user), json=payload)
            assert first.status_code == 201, first.json()
            case_id = first.json()["case"]["id"]
            escalation_id = first.json()["case"]["earlyEscalationId"]
            replay = client.post(path, headers=actor_headers(user), json=payload)
        else:
            created = client.post(
                "/v1/cases",
                headers=actor_headers(user, uuid4()),
                json={
                    "intakeSchemaVersion": "dental-case-intake.v2",
                    "channel": "TELEGRAM",
                },
            )
            assert created.status_code == 201, created.json()
            case_id = created.json()["id"]
            added = client.post(
                f"/v1/cases/{case_id}/facts",
                headers=actor_headers(user, uuid4()),
                json={
                    "questionId": "synthetic_v3_intake",
                    "intakeSchemaVersion": "dental-case-intake.v2",
                    "facts": facts,
                },
            )
            assert added.status_code == 200, added.json()
            path = f"/v1/cases/{case_id}/intake-finalizations"
            headers = actor_headers(user, uuid4())
            first = client.post(path, headers=headers, json={})
            assert first.status_code == 200, first.json()
            escalation_id = first.json()["earlyEscalationId"]
            replay = client.post(path, headers=headers, json={})
        assert replay.status_code == 200
        assert replay.json() == first.json()
        assert escalation_id is not None
        foreign = client.get(f"/v1/case-escalations/{escalation_id}", headers=actor_headers(other))
        assert foreign.status_code in {403, 404}

    async def check():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session, session.begin():
                await session.execute(
                    select(func.set_config("app.current_clinic_id", str(clinic_id), True))
                )
                escalations = (
                    await session.scalars(
                        select(CaseEscalation).where(CaseEscalation.case_id == case_id)
                    )
                ).all()
                assert len(escalations) == 1
                assert escalations[0].level == level
                # Full later verification may record UNAVAILABLE; urgent routing survives.
                domain = _domain_facts(
                    list(
                        (
                            await session.scalars(
                                select(CaseFact).where(CaseFact.case_id == case_id)
                            )
                        ).all()
                    )
                )
                policy = RiskPolicy(
                    version="dental-risk.v3",
                    high_demand_threshold_kopecks=5_000_000,
                    guided_v2_explicit_signals_enabled=True,
                )
                full = evaluate_risk(
                    domain,
                    policy=policy,
                    evidence_verified=False,
                )
                persisted = await record_case_risk_assessment(
                    session,
                    clinic_id=clinic_id,
                    case_id=escalations[0].case_id,
                    actor_membership_id=membership_id,
                    policy_id=policy_id,
                    assessment=full,
                    evidence_trace_sha256="e" * 64,
                )
                stored = await session.get(CaseRiskAssessment, persisted.assessment_id)
                assert stored is not None and stored.level == "UNAVAILABLE"
                early_stored = await session.get(
                    CaseRiskAssessment, escalations[0].case_risk_assessment_id
                )
                assert early_stored is not None
                assert stored.fact_snapshot_sha256 == early_stored.fact_snapshot_sha256
                repeated = evaluate_early_triage(domain, policy=policy)
                assert repeated is not None
                rerun = await record_case_risk_assessment(
                    session,
                    clinic_id=clinic_id,
                    case_id=escalations[0].case_id,
                    actor_membership_id=membership_id,
                    policy_id=policy_id,
                    assessment=repeated,
                    evidence_trace_sha256="f" * 64,
                )
                assert str(rerun.escalation_id) == escalation_id
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(CaseEscalation)
                        .where(CaseEscalation.case_id == case_id)
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(check())
