"""Persist hash-only verifier results for a tenant case without exposing a recommendation."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.models import (
    AuditEvent,
    Case,
    CaseAnalysisClaim,
    CaseAnalysisRun,
    CaseRiskAssessment,
    ClinicUser,
)
from legal_core.verifier import ProposedClaim, VerificationDecision

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ClaimAuditPayload:
    claim_id: str
    claim_kind: str
    claim_sha256: str
    verification_result: str
    reason_code: str | None
    evidence_fragment_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerifierRunPayload:
    verifier_status: str
    block_reason_codes: tuple[str, ...]
    claims: tuple[ClaimAuditPayload, ...]


@dataclass(frozen=True, slots=True)
class PersistedVerifierRun:
    analysis_run_id: UUID


def build_verifier_run_payload(
    claims: Sequence[ProposedClaim], decision: VerificationDecision
) -> VerifierRunPayload:
    """Create storage-safe verifier data and reject a malformed agent/adapter result."""

    decisions_by_id = {claim.claim_id: claim for claim in decision.claims}
    claim_ids = {claim.claim_id for claim in claims}
    if len(decisions_by_id) != len(decision.claims) or set(decisions_by_id) != claim_ids:
        raise ValueError("claim identifiers do not match verifier results")

    payload_claims = tuple(
        ClaimAuditPayload(
            claim_id=claim.claim_id,
            claim_kind=claim.kind.value,
            claim_sha256=hashlib.sha256(claim.text.encode()).hexdigest(),
            verification_result=decisions_by_id[claim.claim_id].result.value,
            reason_code=decisions_by_id[claim.claim_id].reason_code,
            evidence_fragment_ids=tuple(
                str(identifier)
                for identifier in decisions_by_id[claim.claim_id].verified_fragment_ids
            ),
        )
        for claim in claims
    )
    reason_codes = tuple(
        claim.reason_code for claim in payload_claims if claim.reason_code is not None
    )
    return VerifierRunPayload(
        verifier_status="PASSED" if decision.analysis_allowed else "BLOCKED",
        block_reason_codes=reason_codes,
        claims=payload_claims,
    )


async def record_verifier_run(
    session: AsyncSession,
    *,
    clinic_id: UUID,
    case_id: UUID,
    actor_membership_id: UUID,
    case_risk_assessment_id: UUID | None,
    as_of_date: date,
    fact_snapshot_sha256: str,
    evidence_trace_sha256: str,
    payload: VerifierRunPayload,
) -> PersistedVerifierRun:
    """Write one immutable verifier run after tenant-owned references are rechecked."""

    if _SHA256.fullmatch(fact_snapshot_sha256) is None:
        raise ValueError("fact snapshot SHA-256 must be a lowercase 64-character digest")
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
    case = await session.scalar(
        select(Case).where(Case.id == case_id, Case.clinic_id == clinic_id)
    )
    if membership is None or case is None:
        raise PermissionError("active tenant membership and case are required")
    if case_risk_assessment_id is not None:
        risk = await session.scalar(
            select(CaseRiskAssessment).where(
                CaseRiskAssessment.id == case_risk_assessment_id,
                CaseRiskAssessment.clinic_id == clinic_id,
                CaseRiskAssessment.case_id == case_id,
            )
        )
        if risk is None:
            raise LookupError("risk assessment does not belong to the tenant case")

    run = CaseAnalysisRun(
        clinic_id=clinic_id,
        case_id=case_id,
        case_risk_assessment_id=case_risk_assessment_id,
        created_by_membership_id=membership.id,
        as_of_date=as_of_date,
        fact_snapshot_sha256=fact_snapshot_sha256,
        evidence_trace_sha256=evidence_trace_sha256,
        verifier_status=payload.verifier_status,
        block_reason_codes_json=list(payload.block_reason_codes),
    )
    session.add(run)
    await session.flush()
    session.add_all(
        [
            CaseAnalysisClaim(
                clinic_id=clinic_id,
                analysis_run_id=run.id,
                claim_id=claim.claim_id,
                claim_kind=claim.claim_kind,
                claim_sha256=claim.claim_sha256,
                verification_result=claim.verification_result,
                reason_code=claim.reason_code,
                evidence_fragment_ids_json=list(claim.evidence_fragment_ids),
            )
            for claim in payload.claims
        ]
    )
    session.add(
        AuditEvent(
            clinic_id=clinic_id,
            actor_membership_id=membership.id,
            action="VERIFIER_RUN_RECORDED",
            resource_type="CASE_ANALYSIS_RUN",
            resource_id=run.id,
            metadata_json={
                "claimCount": len(payload.claims),
                "evidenceTraceSha256": evidence_trace_sha256,
                "verifierStatus": payload.verifier_status,
            },
            correlation_id=uuid4(),
        )
    )
    return PersistedVerifierRun(analysis_run_id=run.id)
