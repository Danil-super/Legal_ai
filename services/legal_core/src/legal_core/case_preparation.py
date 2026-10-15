"""Core-built case preparation; no legal report exists for intermediate results."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.analysis_clarifications import ClarificationRequest
from legal_core.analysis_contracts import PreparationResult
from legal_core.case_api import (
    ActorContext,
    ApiError,
    _audit,
    _canonical_hash,
    _finish_idempotency,
    _new_idempotency_record,
    _require_case_content_available,
)
from legal_core.case_preparation_contracts import (
    HumanReviewResult,
    LimitedResult,
    NeedsFactsResult,
    PreparationSource,
)
from legal_core.models import AuditEvent, Case, IdempotencyRecord
from legal_core.risk_engine import RiskAssessment, RiskLevel, risk_missing_facts


def intermediate_result(
    *, source: PreparationSource, risk: RiskAssessment, escalation_id: UUID | None,
    clarification_requests: list[ClarificationRequest], analysis_allowed: bool,
) -> PreparationResult | None:
    """Select only from actual deterministic risk/verifier outputs, not model labels."""
    if risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        if escalation_id is None:
            raise ApiError(status_code=500, code="PREPARATION_ESCALATION_MISSING",
                           message="Required escalation is unavailable")
        return PreparationResult(HumanReviewResult(
            resultVersion=2, status="HUMAN_REVIEW", source=source,
            escalationId=escalation_id,
            riskLevel="HIGH" if risk.level == RiskLevel.HIGH else "CRITICAL",
            riskPolicyVersion=risk.policy_version,
        ))
    if clarification_requests:
        return PreparationResult(NeedsFactsResult(
            resultVersion=2, status="NEEDS_FACTS", source=source,
            reasonCode="FACTUAL_CLARIFICATION_REQUIRED",
            clarificationRequests=clarification_requests,
        ))
    if not analysis_allowed:
        missing = [item.fact_key for item in risk_missing_facts(risk)]
        return PreparationResult(LimitedResult(
            resultVersion=2, status="LIMITED", source=source,
            reasonCode="INSUFFICIENT_FACTS" if missing else "EVIDENCE_NOT_VERIFIED",
            missingFactKeys=missing,
        ))
    return None


async def read_preparation_replay(
    session: AsyncSession, *, actor: ActorContext, case: Case, scope: str,
    idempotency_key: UUID, wire: dict[str, Any],
) -> PreparationResult:
    """A mutable cache alone is not a source binding; match its retained audit receipt."""
    try:
        result = PreparationResult.model_validate(wire)
        record = await session.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.clinic_id == actor.clinic_id,
            IdempotencyRecord.actor_membership_id == actor.membership_id,
            IdempotencyRecord.scope == scope, IdempotencyRecord.key == str(idempotency_key),
        ))
        audit = await session.scalar(select(AuditEvent.id).where(
            AuditEvent.clinic_id == actor.clinic_id,
            AuditEvent.actor_membership_id == actor.membership_id,
            AuditEvent.resource_id == case.id,
            AuditEvent.action == "CASE_PREPARATION_COMPLETED",
            AuditEvent.metadata_json["operationId"].as_string()
            == str(result.root.source.operation_id),
            AuditEvent.metadata_json["resultSha256"].as_string() == _canonical_hash(wire),
        ))
        if (
            record is None or record.resource_type != "CASE_PREPARATION"
            or record.resource_id != case.id or result.root.source.case_id != case.id
            or audit is None
        ):
            raise ValueError("preparation receipt binding mismatch")
        _require_case_content_available(case)
        return result
    except ValueError as error:
        raise ApiError(status_code=502, code="PREPARATION_RESULT_INVALID",
                       message="Stored preparation is unavailable") from error


async def store_preparation_result(
    session: AsyncSession, *, actor: ActorContext, case: Case,
    scope: str, idempotency_key: UUID, request_hash: str, result: PreparationResult,
) -> None:
    _require_case_content_available(case)
    if case.clinic_id != actor.clinic_id or result.root.source.case_id != case.id:
        raise ApiError(status_code=404, code="CASE_NOT_FOUND", message="Case not found")
    wire = result.model_dump(mode="json", by_alias=True)
    record = _new_idempotency_record(actor=actor, scope=scope, key=idempotency_key,
                                     request_hash=request_hash)
    session.add(record)
    _finish_idempotency(record, resource_type="CASE_PREPARATION", resource_id=case.id,
                        response_json=wire)
    session.add(_audit(
        actor=actor, action="CASE_PREPARATION_COMPLETED", resource_type="CASE_PREPARATION",
        resource_id=case.id, metadata={
            "status": result.root.status,
            "operationId": str(result.root.source.operation_id),
            "resultSha256": _canonical_hash(wire),
            "factSnapshotSha256": result.root.source.fact_snapshot_sha256,
            "clarificationRequestCount": len(result.root.clarification_requests)
            if isinstance(result.root, NeedsFactsResult) else 0,
        },
    ))
