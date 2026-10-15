"""Core-built case preparation; no legal report exists for intermediate results."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
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
    resolve_actor,
)
from legal_core.case_preparation_contracts import (
    HumanReviewResult,
    LimitedResult,
    NeedsFactsResult,
    PreparationSource,
)
from legal_core.contracts import FactKey
from legal_core.factual_safety_intake import SCREENING_FIELDS
from legal_core.models import AuditEvent, Case, IdempotencyRecord, SubscriptionEntitlement
from legal_core.risk_engine import RiskAssessment, RiskLevel, risk_missing_facts


def precontext_limitation(
    *, error: ApiError, source: PreparationSource, facts: dict[FactKey, object],
) -> PreparationResult:
    """Only explicit Core factual/evidence gates become limitations, never arbitrary errors."""
    reason = error.code
    keys = error.details.get("missingFactKeys", [])
    if reason == "INSUFFICIENT_FACTS":
        event = facts.get(FactKey.EVENT_DATE)
        if (
            keys == [FactKey.EVENT_DATE.value] and isinstance(event, dict)
            and event.get("precision") in {"UNKNOWN", "APPROXIMATE"}
        ):
            reason = "ANALYSIS_DATE_UNCERTAIN"
    elif reason in {
        "FACTUAL_SAFETY_SCREENING_UNKNOWN", "FACTUAL_SAFETY_AMOUNT_UNKNOWN",
        *(f"FACTUAL_SAFETY_{field.upper()}_UNKNOWN" for field in SCREENING_FIELDS),
    }:
        reason = "FACTUAL_SAFETY_UNCERTAIN"
        keys = [FactKey.FACTUAL_SAFETY_SCREENING.value]
    elif reason not in {"ANALYSIS_DATE_UNCERTAIN", "LEGAL_EVIDENCE_UNAVAILABLE"}:
        raise error
    return PreparationResult(LimitedResult.model_validate({
        "resultVersion": 2, "status": "LIMITED", "source": source,
        "reasonCode": reason, "missingFactKeys": keys,
    }))


async def recheck_submission_access(
    session: AsyncSession, *, actor: ActorContext, telegram_user_id: int, case: Case,
) -> None:
    """Lock waits must not preserve withdrawn access or transaction-start expiry."""
    if await resolve_actor(session, telegram_user_id) != actor:
        raise ApiError(status_code=403, code="ACTOR_CONTEXT_CHANGED", message="Access changed")
    entitlement = await session.scalar(select(SubscriptionEntitlement.id).where(
        SubscriptionEntitlement.clinic_id == actor.clinic_id,
        SubscriptionEntitlement.user_id == actor.user_id,
        SubscriptionEntitlement.status == "ACTIVE",
        SubscriptionEntitlement.starts_at <= func.clock_timestamp(),
        (SubscriptionEntitlement.ends_at.is_(None))
        | (SubscriptionEntitlement.ends_at > func.clock_timestamp()),
    ))
    if entitlement is None:
        raise ApiError(status_code=403, code="SUBSCRIPTION_INACTIVE", message="Access is inactive")
    _require_case_content_available(case)


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
    session: AsyncSession, *, clinic_id: UUID, actor_membership_id: UUID, case: Case, scope: str,
    idempotency_key: UUID, wire: dict[str, Any],
) -> PreparationResult:
    """A mutable cache alone is not a source binding; match its retained audit receipt."""
    try:
        result = PreparationResult.model_validate(wire)
        record = await session.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.clinic_id == clinic_id,
            IdempotencyRecord.actor_membership_id == actor_membership_id,
            IdempotencyRecord.scope == scope, IdempotencyRecord.key == str(idempotency_key),
        ))
        if record is None:
            raise ValueError("preparation receipt missing")
        audit = await session.scalar(select(AuditEvent.id).where(
            AuditEvent.clinic_id == clinic_id,
            AuditEvent.actor_membership_id == actor_membership_id,
            AuditEvent.resource_id == case.id,
            AuditEvent.action == "CASE_PREPARATION_COMPLETED",
            AuditEvent.metadata_json["operationId"].as_string()
            == str(result.root.source.operation_id),
            AuditEvent.metadata_json["resultSha256"].as_string() == _canonical_hash(wire),
            AuditEvent.metadata_json["requestBindingSha256"].as_string() == _canonical_hash({
                "scope": scope, "key": str(idempotency_key),
                "requestSha256": record.request_sha256,
            }),
        ))
        if (
            case.clinic_id != clinic_id or record.resource_type != "CASE_PREPARATION"
            or record.resource_id != case.id or result.root.source.case_id != case.id
            or (result.root.source.context is None and result.root.source.operation_id != record.id)
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
    if result.root.source.context is None:
        # Before legal retrieval there is no verifier run. The durable operation
        # is this actual receipt row, not a fabricated analysis-run identity.
        record.id = result.root.source.operation_id
    session.add(record)
    _finish_idempotency(record, resource_type="CASE_PREPARATION", resource_id=case.id,
                        response_json=wire)
    session.add(_audit(
        actor=actor, action="CASE_PREPARATION_COMPLETED", resource_type="CASE_PREPARATION",
        resource_id=case.id, metadata={
            "status": result.root.status,
            "operationId": str(result.root.source.operation_id),
            "resultSha256": _canonical_hash(wire),
            "requestBindingSha256": _canonical_hash({
                "scope": scope, "key": str(idempotency_key), "requestSha256": request_hash,
            }),
            "factSnapshotSha256": result.root.source.fact_snapshot_sha256,
            "clarificationRequestCount": len(result.root.clarification_requests)
            if isinstance(result.root, NeedsFactsResult) else 0,
        },
    ))
