"""Persist deterministic risk results without enabling an external patient draft."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.contracts import FactKey
from legal_core.models import (
    AuditEvent,
    Case,
    CaseEscalation,
    CaseRiskAssessment,
    ClinicUser,
    RiskPolicyVersion,
)
from legal_core.risk_engine import RiskAssessment, RiskLevel, evaluate_early_triage
from legal_core.risk_policy_repository import ApprovedRiskPolicyRepository

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


async def ensure_early_triage(
    session: AsyncSession,
    *,
    clinic_id: UUID,
    case_id: UUID,
    actor_membership_id: UUID,
    facts: dict[FactKey, object],
) -> UUID | None:
    """Persist safety routing independently of retrieval/models under an approved v2 policy."""
    await session.scalar(
        select(Case).where(Case.id == case_id, Case.clinic_id == clinic_id).with_for_update()
    )
    try:
        policy = await ApprovedRiskPolicyRepository(session).get()
    except LookupError:
        return None
    if not policy.early_triage_enabled:
        return None
    assessment = evaluate_early_triage(facts, policy=policy.domain)
    if assessment is None:
        return None
    previous = await session.scalar(
        select(CaseEscalation)
        .join(CaseRiskAssessment, CaseRiskAssessment.id == CaseEscalation.case_risk_assessment_id)
        .where(
            CaseEscalation.clinic_id == clinic_id,
            CaseEscalation.case_id == case_id,
            CaseRiskAssessment.fact_snapshot_sha256 == assessment.fact_snapshot_sha256,
            CaseRiskAssessment.policy_id == policy.id,
        )
    )
    if previous is not None:
        return previous.id
    result = await record_case_risk_assessment(
        session,
        clinic_id=clinic_id,
        case_id=case_id,
        actor_membership_id=actor_membership_id,
        policy_id=policy.id,
        assessment=assessment,
        evidence_trace_sha256=hashlib.sha256(b"EARLY_TRIAGE_NO_LEGAL_EVIDENCE").hexdigest(),
    )
    return result.escalation_id


@dataclass(frozen=True, slots=True)
class PersistedRiskAssessment:
    assessment_id: UUID
    escalation_id: UUID | None


async def record_case_risk_assessment(
    session: AsyncSession,
    *,
    clinic_id: UUID,
    case_id: UUID,
    actor_membership_id: UUID,
    policy_id: UUID,
    assessment: RiskAssessment,
    evidence_trace_sha256: str,
) -> PersistedRiskAssessment:
    """Store an append-only assessment after server-side tenant/policy checks."""

    if _SHA256.fullmatch(evidence_trace_sha256) is None:
        raise ValueError("evidence trace SHA-256 must be a lowercase 64-character digest")

    await session.execute(select(func.set_config("app.current_clinic_id", str(clinic_id), True)))
    membership = await session.scalar(
        select(ClinicUser).where(
            ClinicUser.id == actor_membership_id,
            ClinicUser.clinic_id == clinic_id,
            ClinicUser.status == "ACTIVE",
        )
    )
    if membership is None:
        raise PermissionError("active tenant membership is required to record risk")
    case = await session.scalar(
        select(Case).where(Case.id == case_id, Case.clinic_id == clinic_id).with_for_update()
    )
    if case is None:
        raise LookupError("case does not exist in the current tenant")
    policy = await session.scalar(
        select(RiskPolicyVersion).where(
            RiskPolicyVersion.id == policy_id,
            RiskPolicyVersion.status == "APPROVED",
        )
    )
    if policy is None:
        raise PermissionError("an approved risk policy is required")
    expected_policy_version = f"{policy.policy_key}.v{policy.version}"
    if assessment.policy_version != expected_policy_version:
        raise ValueError("assessment policy version does not match the approved policy")

    persisted = CaseRiskAssessment(
        clinic_id=clinic_id,
        case_id=case_id,
        policy_id=policy.id,
        level=assessment.level.value,
        reason_codes_json=list(assessment.reason_codes),
        fact_snapshot_sha256=assessment.fact_snapshot_sha256,
        evidence_trace_sha256=evidence_trace_sha256,
        # Even LOW risk is internal-only until the report/evidence release is explicitly enabled.
        external_draft_allowed=False,
    )
    session.add(persisted)
    await session.flush()

    escalation_id: UUID | None = None
    if assessment.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        escalation = await session.scalar(
            select(CaseEscalation)
            .join(
                CaseRiskAssessment,
                CaseRiskAssessment.id == CaseEscalation.case_risk_assessment_id,
            )
            .where(
                CaseEscalation.clinic_id == clinic_id,
                CaseEscalation.case_id == case.id,
                CaseEscalation.level == assessment.level.value,
                CaseRiskAssessment.policy_id == policy.id,
                CaseRiskAssessment.fact_snapshot_sha256 == assessment.fact_snapshot_sha256,
            )
            .order_by(CaseEscalation.created_at.desc())
            .limit(1)
        )
        if escalation is None:
            escalation = CaseEscalation(
                clinic_id=clinic_id,
                case_id=case.id,
                case_risk_assessment_id=persisted.id,
                level=assessment.level.value,
                reason_codes_json=list(assessment.reason_codes),
            )
            session.add(escalation)
            await session.flush()
        escalation_id = escalation.id

    session.add(
        AuditEvent(
            clinic_id=clinic_id,
            actor_membership_id=membership.id,
            action="RISK_ASSESSMENT_RECORDED",
            resource_type="CASE_RISK_ASSESSMENT",
            resource_id=persisted.id,
            metadata_json={
                "evidenceTraceSha256": evidence_trace_sha256,
                "policyId": str(policy.id),
                "reasonCodes": list(assessment.reason_codes),
                "riskLevel": assessment.level.value,
            },
            correlation_id=uuid4(),
        )
    )
    return PersistedRiskAssessment(assessment_id=persisted.id, escalation_id=escalation_id)
