import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from legal_core.contracts import FactKey
from legal_core.database import database_url
from legal_core.models import (
    AuditEvent,
    Case,
    CaseEscalation,
    CaseRiskAssessment,
    Clinic,
    ClinicUser,
    RiskPolicyEvent,
    RiskPolicyVersion,
    User,
)
from legal_core.risk_engine import RiskPolicy, evaluate_risk
from legal_core.risk_persistence import record_case_risk_assessment
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL risk-persistence tests",
)


def _policy_payload() -> tuple[dict[str, object], str]:
    payload: dict[str, object] = {"highDemandThresholdKopecks": 10_000_000}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload, digest


def test_high_risk_assessment_is_tenant_scoped_immutable_and_escalated() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid4().int % 10**8
        policy_key = f"risk-policy-{uuid4().hex}"
        payload, content_sha256 = _policy_payload()
        try:
            async with factory() as session, session.begin():
                clinic = Clinic(name="Synthetic risk clinic")
                actor = User(
                    telegram_user_id=int(f"84{suffix:08d}"),
                    system_role=None,
                    display_name="Synthetic clinic administrator",
                )
                editor = User(
                    telegram_user_id=int(f"85{suffix:08d}"),
                    system_role="LEGAL_EDITOR",
                    display_name="Synthetic legal editor",
                )
                session.add_all([clinic, actor, editor])
                await session.flush()
                membership = ClinicUser(
                    clinic_id=clinic.id,
                    user_id=actor.id,
                    role="CLINIC_ADMIN",
                )
                session.add(membership)
                await session.flush()
                await session.execute(
                    select(func.set_config("app.current_clinic_id", str(clinic.id), True))
                )
                case = Case(
                    clinic_id=clinic.id,
                    created_by_membership_id=membership.id,
                    status="ANALYZING",
                )
                session.add(case)
                policy = RiskPolicyVersion(
                    policy_key=policy_key,
                    version=1,
                    policy_json=payload,
                    content_sha256=content_sha256,
                    created_by_user_id=editor.id,
                )
                session.add(policy)
                await session.flush()
                session.add(
                    RiskPolicyEvent(
                        risk_policy_id=policy.id,
                        actor_user_id=editor.id,
                        decision="APPROVED",
                        expected_content_sha256=content_sha256,
                        reason_code="SYNTHETIC_TEST",
                    )
                )
                await session.flush()
                policy.status = "APPROVED"
                policy.approved_by_user_id = editor.id
                policy.approved_at = datetime.now(UTC)
                clinic_id = clinic.id
                case_id = case.id
                membership_id = membership.id
                policy_id = policy.id

            assessment = evaluate_risk(
                {
                    FactKey.HARM_CLAIMED: "NO",
                    FactKey.LAWYER_CONTACT: "NO",
                    FactKey.FORMAL_CLAIM: "YES",
                    FactKey.REGULATOR_OR_COURT: "NO",
                    FactKey.REGULATOR_THREAT: "NO",
                    FactKey.CLINIC_DOCUMENTS: {"CONTRACT": "AVAILABLE"},
                },
                policy=RiskPolicy(
                    version=f"{policy_key}.v1", high_demand_threshold_kopecks=10_000_000
                ),
                evidence_verified=True,
            )
            async with factory() as session, session.begin():
                persisted = await record_case_risk_assessment(
                    session,
                    clinic_id=clinic_id,
                    case_id=case_id,
                    actor_membership_id=membership_id,
                    policy_id=policy_id,
                    assessment=assessment,
                    evidence_trace_sha256="e" * 64,
                )

            async with factory() as session, session.begin():
                await session.execute(
                    select(func.set_config("app.current_clinic_id", str(clinic_id), True))
                )
                stored = await session.get(CaseRiskAssessment, persisted.assessment_id)
                escalation = await session.get(CaseEscalation, persisted.escalation_id)
                audit = await session.scalar(
                    select(AuditEvent).where(AuditEvent.resource_id == persisted.assessment_id)
                )
                assert stored is not None
                assert stored.level == "HIGH"
                assert stored.external_draft_allowed is False
                assert escalation is not None
                assert escalation.level == "HIGH"
                assert audit is not None
                assert audit.metadata_json == {
                    "evidenceTraceSha256": "e" * 64,
                    "policyId": str(policy_id),
                    "reasonCodes": ["FORMAL_CLAIM_RECEIVED"],
                    "riskLevel": "HIGH",
                }
        finally:
            await engine.dispose()

    asyncio.run(scenario())
