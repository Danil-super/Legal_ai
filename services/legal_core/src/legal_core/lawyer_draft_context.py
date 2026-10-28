"""Fenced read-only context for internal AI assistance on an already routed case."""

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis_api import (
    AnalysisState,
    _analysis_date,
    _context_response,
    _require_agent_internal_key,
    _retrieve_analysis_state,
)
from legal_core.analysis_jobs import analysis_result_scope, require_job_lease
from legal_core.analysis_safe_stop import require_analysis_running
from legal_core.case_api import (
    ActorContext,
    ApiError,
    IdempotencyKey,
    TelegramUserId,
    _audit,
    _current_fact_rows,
    _domain_facts,
    _escalation_state,
    _require_case_intake_actor,
    _require_finalized_case,
    _tenant_case,
    resolve_actor,
)
from legal_core.case_preparation import read_preparation_replay, recheck_submission_access
from legal_core.case_preparation_contracts import (
    HumanReviewResult,
    PreparationContext,
    PreparationSource,
)
from legal_core.lawyer_draft_contracts import LawyerDraftContextRequest, LawyerDraftContextResponse
from legal_core.lawyer_draft_freshness import require_lawyer_draft_sources_current
from legal_core.models import Case, CaseEscalation, CaseRiskAssessment, IdempotencyRecord
from legal_core.pseudonymization import pseudonymize_text
from legal_core.reference_analogy_freshness import require_reference_analogy_trace_current
from legal_core.risk_engine import fact_snapshot_sha256
from legal_core.risk_policy_repository import ApprovedRiskPolicy, ApprovedRiskPolicyRepository


def _require_enabled() -> None:
    if os.getenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "0").strip().lower() not in {"true", "1"}:
        raise ApiError(status_code=503, code="LAWYER_DRAFT_DISABLED",
                       message="Internal lawyer-draft context is not enabled")


def _require_creator(actor: ActorContext, case: Case) -> None:
    if case.created_by_membership_id != actor.membership_id:
        raise ApiError(status_code=404, code="CASE_NOT_FOUND", message="Case not found")


def _stale() -> ApiError:
    return ApiError(status_code=409, code="LAWYER_DRAFT_CONTEXT_STALE",
                    message="Retained preparation no longer matches the current case or policy")


async def _policy(session: AsyncSession) -> ApprovedRiskPolicy:
    try:
        return await ApprovedRiskPolicyRepository(session).get()
    except (LookupError, ValueError):
        pass
    raise ApiError(status_code=409, code="RISK_POLICY_NOT_READY",
                   message="An approved risk policy is not available")


@dataclass(frozen=True)
class LawyerDraftContextState:
    """Locked fresh inputs for a caller-owned transaction; never commits itself."""

    response: LawyerDraftContextResponse
    analysis_state: AnalysisState
    actor: ActorContext


async def load_lawyer_draft_context(
    session: AsyncSession, *, case_id: UUID, telegram_user_id: int, idempotency_key: UUID,
    job_id: UUID | None, job_token: UUID | None, request: LawyerDraftContextRequest,
) -> LawyerDraftContextState:
    _require_enabled()
    require_analysis_running()
    if job_id is None or job_token is None or idempotency_key != job_id:
        raise ApiError(status_code=409, code="ANALYSIS_JOB_LEASE_EXPIRED",
                       message="Invalid analysis job lease")
    actor = await resolve_actor(session, telegram_user_id)
    _require_case_intake_actor(actor)
    case = await _tenant_case(session, actor, case_id)
    _require_creator(actor, case)
    # Retention/assignment use Case before dependent rows. A creator change during
    # the actual wait must not retain access from the first unlocked read.
    case = await _tenant_case(session, actor, case_id, for_update=True)
    _require_creator(actor, case)
    _require_finalized_case(case)
    if case.status != "ESCALATION_REQUIRED":
        raise ApiError(status_code=409, code="CASE_ESCALATION_NOT_ACTIVE",
                       message="Case is not awaiting lawyer review")
    job = await require_job_lease(session, actor, case_id, job_id, job_token)
    if job.result_version != 2:
        raise ApiError(status_code=409, code="ANALYSIS_RESULT_VERSION_MISMATCH",
                       message="Analysis job result version mismatch")
    await recheck_submission_access(session, actor=actor, telegram_user_id=telegram_user_id,
                                    case=case)

    scope = analysis_result_scope(case_id, 2)
    receipt = await session.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.clinic_id == actor.clinic_id,
        IdempotencyRecord.actor_membership_id == actor.membership_id,
        IdempotencyRecord.scope == scope, IdempotencyRecord.key == str(job_id),
        IdempotencyRecord.state == "SUCCEEDED",
    ))
    if receipt is None or receipt.response_json is None:
        raise ApiError(status_code=409, code="LAWYER_DRAFT_SOURCE_UNAVAILABLE",
                       message="A retained human-review preparation is required")
    replay = await read_preparation_replay(
        session, clinic_id=actor.clinic_id, actor_membership_id=actor.membership_id,
        case=case, scope=scope, idempotency_key=job_id, wire=receipt.response_json,
    )
    retained = replay.root
    if not isinstance(retained, HumanReviewResult):
        raise ApiError(status_code=409, code="LAWYER_DRAFT_SOURCE_UNAVAILABLE",
                       message="A retained human-review preparation is required")
    facts = _domain_facts(await _current_fact_rows(session, case_id))
    facts_hash = fact_snapshot_sha256(facts)
    policy = await _policy(session)
    if (retained.source.operation_id != request.expected_preparation_operation_id
            or retained.source.fact_snapshot_sha256 != request.expected_fact_snapshot_sha256
            or facts_hash != retained.source.fact_snapshot_sha256
            or retained.risk_policy_version != request.expected_risk_policy_version
            or policy.domain.version != retained.risk_policy_version):
        raise _stale()
    escalation = await session.scalar(select(CaseEscalation).join(
        CaseRiskAssessment, CaseRiskAssessment.id == CaseEscalation.case_risk_assessment_id,
    ).where(
        CaseEscalation.id == retained.escalation_id,
        CaseEscalation.clinic_id == actor.clinic_id, CaseEscalation.case_id == case.id,
        CaseEscalation.level == retained.risk_level,
        CaseRiskAssessment.clinic_id == actor.clinic_id,
        CaseRiskAssessment.case_id == case.id,
        CaseRiskAssessment.fact_snapshot_sha256 == facts_hash,
        CaseRiskAssessment.policy_id == policy.id,
    ).with_for_update(of=CaseEscalation).execution_options(populate_existing=True))
    if escalation is None:
        raise _stale()
    if (await _escalation_state(session, actor, escalation.id))[0] == "RESOLVED":
        raise ApiError(status_code=409, code="ESCALATION_RESOLVED",
                       message="The escalation has already been resolved")
    if pseudonymize_text(json.dumps({key.value: value for key, value in facts.items()},
                                    ensure_ascii=False, allow_nan=False)).changed:
        raise ApiError(status_code=422, code="LAWYER_DRAFT_CONTEXT_INVALID",
                       message="De-identified case facts are required")

    state = await _retrieve_analysis_state(session, actor=actor, case=case, facts=facts,
                                           as_of_date=_analysis_date(facts), policy=policy)
    await require_lawyer_draft_sources_current(session, actor=actor, state=state)
    await require_reference_analogy_trace_current(
        session, case=case, facts=facts, expected_trace=state.reference_example_trace_sha256,
    )
    await recheck_submission_access(session, actor=actor, telegram_user_id=telegram_user_id,
                                    case=case)
    await require_job_lease(session, actor, case_id, job_id, job_token)
    _require_creator(actor, case)
    current_facts = _domain_facts(await _current_fact_rows(session, case_id))
    if (fact_snapshot_sha256(current_facts) != facts_hash
            or (await _policy(session)).id != policy.id):
        raise _stale()
    _require_enabled()
    require_analysis_running()
    context = _context_response(state)
    result = LawyerDraftContextResponse(
        schemaVersion="lawyer-draft-context.v1", source=PreparationSource(
            operationId=retained.source.operation_id, caseId=case.id,
            factSnapshotSha256=facts_hash, context=PreparationContext(
                asOfDate=state.as_of_date, evidenceTraceSha256=state.evidence_trace_sha256,
                clinicDocumentContextTraceSha256=state.clinic_document_context_trace_sha256,
                riskPolicyVersion=policy.domain.version,
                referenceExampleTraceSha256=state.reference_example_trace_sha256,
            ),
        ), escalationId=escalation.id, riskLevel=retained.risk_level, context=context,
    )
    session.add(_audit(actor=actor, action="LAWYER_DRAFT_CONTEXT_READ", resource_type="CASE",
                       resource_id=case.id, metadata={
                           "jobId": str(job_id), "escalationId": str(escalation.id),
                           "preparationOperationId": str(retained.source.operation_id),
                           "factSnapshotSha256": facts_hash,
                           "evidenceTraceSha256": state.evidence_trace_sha256,
                           "clinicDocumentContextTraceSha256": (
                               state.clinic_document_context_trace_sha256
                           ),
                           "riskPolicyVersion": policy.domain.version,
                       }))
    return LawyerDraftContextState(response=result, analysis_state=state, actor=actor)


def create_lawyer_draft_context_router(sessions: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter(prefix="/v1/cases", tags=["case-analysis"],
                       dependencies=[Depends(_require_agent_internal_key)])

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    @router.post("/{case_id}/lawyer-draft-context", response_model=LawyerDraftContextResponse)
    async def context(
        case_id: UUID, request: LawyerDraftContextRequest, telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Annotated[AsyncSession, Depends(session_dependency)],
        x_analysis_job_id: Annotated[UUID | None, Header()] = None,
        x_analysis_job_token: Annotated[UUID | None, Header()] = None,
    ) -> LawyerDraftContextResponse:
        loaded = await load_lawyer_draft_context(
            session, case_id=case_id, telegram_user_id=telegram_user_id,
            idempotency_key=idempotency_key, job_id=x_analysis_job_id,
            job_token=x_analysis_job_token, request=request,
        )
        await session.commit()
        return loaded.response

    return router
