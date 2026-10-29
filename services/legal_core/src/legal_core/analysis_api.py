"""Tenant-scoped REST boundary for trusted agent analysis submissions."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis import analyze_frozen_case, evidence_trace_sha256
from legal_core.analysis_clarifications import (
    complete_service_date_requests,
    validate_request_scope,
)
from legal_core.analysis_contracts import (
    AnalysisContextResponse,
    AnalysisSubmissionRequest,
    AnalysisSubmissionResponse,
    ClinicDocumentContextResponse,
    ClinicDocumentReadinessResponse,
    PreparationResult,
    ReportReadyResult,
)
from legal_core.analysis_freshness import (
    AnalysisContextFingerprint,
    analysis_context_is_stale,
)
from legal_core.case_material_context import (
    CaseMaterialContextState, load_case_material_context, require_case_material_context_current,
)
from legal_core.analysis_safe_stop import require_analysis_running
from legal_core.api_contracts import LegalFragmentResponse, ReportResponse
from legal_core.case_api import (
    ActorContext,
    ApiError,
    IdempotencyKey,
    TelegramUserId,
    _audit,
    _canonical_hash,
    _case_response,
    _current_fact_rows,
    _domain_facts,
    _finish_idempotency,
    _idempotency_replay,
    _new_idempotency_record,
    _require_case_intake_actor,
    _require_finalized_case,
    _tenant_case,
    resolve_actor,
)
from legal_core.case_preparation import (
    intermediate_result,
    precontext_limitation,
    read_preparation_replay,
    recheck_submission_access,
    store_preparation_result,
)
from legal_core.case_preparation_contracts import (
    HumanReviewResult,
    PreparationContext,
    PreparationSource,
)
from legal_core.clinic_document_readiness import (
    ClinicDocumentReadiness,
    ClinicDocumentReadinessStatus,
    assess_clinic_document_readiness,
    plan_clinic_document_expectations,
)
from legal_core.clinic_document_retrieval import (
    ApprovedClinicDocumentContextRepository,
    ApprovedClinicDocumentFragment,
    clinic_document_context_trace_sha256,
    plan_clinic_document_queries,
    retrieve_planned_clinic_context,
)
from legal_core.contracts import (
    CanonicalReport,
    CaseStatus,
    ClinicDocumentReadinessCard,
    FactKey,
)
from legal_core.intake import missing_facts_for
from legal_core.factual_safety_risk import confirmed_screening, screening_blocker
from legal_core.legal_conclusions import select_verified_legal_conclusions
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository, ApprovedLegalFragment
from legal_core.models import Case, CaseReport, IdempotencyRecord
from legal_core.reports import build_analysis_report, build_intake_report, render_report_pdf
from legal_core.retrieval_plan import plan_legal_queries, retrieve_planned_evidence
from legal_core.risk_engine import RiskLevel, evaluate_early_triage, fact_snapshot_sha256
from legal_core.risk_persistence import record_case_risk_assessment
from legal_core.risk_policy_repository import ApprovedRiskPolicy, ApprovedRiskPolicyRepository
from legal_core.reference_analogy_contracts import ReferenceAnalogyContext
from legal_core.reference_analogy_freshness import require_reference_analogy_trace_current
from legal_core.reference_analogy_retrieval import select_reference_analogies
from legal_core.risk_engine import risk_missing_facts
from legal_core.verifier import (
    ClaimKind,
    ProposedClaim,
    SemanticReview,
    SemanticVerdict,
    VerificationResult,
)
from legal_core.verifier_persistence import build_verifier_run_payload, record_verifier_run

AgentInternalKeyHeader = Annotated[
    str | None,
    Header(alias="X-Agent-Internal-Key", max_length=512),
]


@dataclass(frozen=True, slots=True)
class AnalysisState:
    case: Case
    facts: dict[FactKey, object]
    as_of_date: date
    evidence: tuple[ApprovedLegalFragment, ...]
    clinic_document_context: tuple[ApprovedClinicDocumentFragment, ...]
    clinic_document_readiness: tuple[ClinicDocumentReadiness, ...]
    fact_snapshot_sha256: str
    evidence_trace_sha256: str
    clinic_document_context_trace_sha256: str
    risk_policy: ApprovedRiskPolicy
    reference_analogies: ReferenceAnalogyContext | None = None
    case_material_context: CaseMaterialContextState = field(
        default_factory=CaseMaterialContextState,
    )

    @property
    def case_material_context_trace_sha256(self) -> str | None:
        return self.case_material_context.trace_sha256

    @property
    def reference_example_trace_sha256(self) -> str | None:
        return self.reference_analogies.trace_sha256 if self.reference_analogies else None


async def _commit_analysis_result(session: AsyncSession, state: AnalysisState) -> None:
    # Flush risk/report/receipt writes (including their actual FK waits) before
    # the last wall-clock/source check. No new writes or waits may follow it.
    await session.flush()
    await require_reference_analogy_trace_current(
        session, case=state.case, facts=state.facts,
        expected_trace=state.reference_example_trace_sha256,
    )
    await require_case_material_context_current(
        session, clinic_id=state.case.clinic_id, case_id=state.case.id,
        state=state.case_material_context,
    )
    await session.commit()


def _require_agent_internal_key(key: AgentInternalKeyHeader = None) -> None:
    """Require the service credential before exposing any analysis boundary."""

    expected = os.getenv("AGENT_INTERNAL_KEY", "")
    if len(expected) < 32 or key is None or not hmac.compare_digest(
        expected.encode("utf-8"), key.encode("utf-8"),
    ):
        raise ApiError(status_code=403, code="INTERNAL_ACCESS_REQUIRED", message="Forbidden")


def _exact_date(value: object) -> date | None:
    if not isinstance(value, dict):
        return None
    if value.get("precision") not in {"EXACT", "APPROXIMATE"}:
        return None
    raw = value.get("date")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _analysis_date(facts: dict[FactKey, object]) -> date:
    candidate_keys = (
        (FactKey.EVENT_DATE,)
        if facts.get(FactKey.INTAKE_VERSION) == "GUIDED_V2"
        else (FactKey.CLAIM_DATE, FactKey.INCIDENT_DATE, FactKey.SERVICE_DATE)
    )
    for key in candidate_keys:
        value = facts.get(key)
        resolved = _exact_date(value)
        if resolved is not None:
            if isinstance(value, dict) and value.get("precision") == "APPROXIMATE":
                # Preserve the selected event's priority. A different exact event date cannot
                # resolve uncertainty about this event or the legal revision applicable to it.
                raise ApiError(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    code="ANALYSIS_DATE_UNCERTAIN",
                    message="An exact case date is required to select the applicable legal version",
                    details={"factKey": key.value, "precision": "APPROXIMATE"},
                )
            return resolved
    raise ApiError(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="ANALYSIS_DATE_UNCERTAIN",
        message="An exact case date is required to select the applicable legal version",
        details={"missingFactKeys": [key.value for key in candidate_keys]},
    )


def _require_analysis_eligible_case(case: Case) -> None:
    _require_finalized_case(case)
    if case.status != CaseStatus.ANALYSIS_BLOCKED.value:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="CASE_ANALYSIS_ALREADY_COMPLETED",
            message="A completed case must not be analysed again without a new intake",
        )
    if case.retention_due_at is not None and case.retention_due_at <= datetime.now(UTC):
        raise ApiError(status_code=410, code="CASE_CONTENT_EXPIRED",
                       message="Case content expired")


async def _load_analysis_state(session: AsyncSession, actor: Any, case_id: UUID) -> AnalysisState:
    case = await _tenant_case(session, actor, case_id)
    _require_analysis_eligible_case(case)
    require_analysis_running()
    facts = _domain_facts(await _current_fact_rows(session, case.id))
    missing = missing_facts_for(facts)
    if missing:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="INSUFFICIENT_FACTS",
            message="Case analysis requires a complete intake",
            details={"missingFactKeys": [item.fact_key.value for item in missing]},
        )

    as_of_date = _analysis_date(facts)
    policy_repository = ApprovedRiskPolicyRepository(session)
    try:
        policy = await policy_repository.get()
    except (LookupError, ValueError) as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="RISK_POLICY_NOT_READY",
            message="An approved risk policy is not available",
        ) from exc

    if (
        facts.get(FactKey.INTAKE_VERSION) == "GUIDED_V2"
        and policy.domain.factual_safety_intake_enabled
        and evaluate_early_triage(facts, policy=policy.domain) is None
    ):
        reason = screening_blocker(facts, confirmed_screening(facts))
        if reason is not None:
            raise ApiError(status_code=422, code=reason,
                           message="A factual clarification is required before analysis")

    state = await _retrieve_analysis_state(session, actor=actor, case=case, facts=facts,
                                           as_of_date=as_of_date, policy=policy)
    await require_reference_analogy_trace_current(
        session, case=case, facts=facts, expected_trace=state.reference_example_trace_sha256,
    )
    return state


async def _retrieve_analysis_state(
    session: AsyncSession, *, actor: ActorContext, case: Case, facts: dict[FactKey, object],
    as_of_date: date, policy: ApprovedRiskPolicy,
) -> AnalysisState:
    """Assemble approved evidence only; callers own their distinct authorization/gates.

    Canonical analysis still enters through _load_analysis_state. The separate
    lawyer-draft context first proves an active audited HUMAN_REVIEW and job lease.
    """
    evidence_repository = ApprovedLegalCorpusRepository(session)
    queries = plan_legal_queries(facts)
    evidence = tuple(
        await retrieve_planned_evidence(
            evidence_repository,
            queries=queries,
            as_of_date=as_of_date,
        )
    )
    if not evidence:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="LEGAL_EVIDENCE_UNAVAILABLE",
            message="No approved date-applicable evidence was found for this case",
        )

    clinic_repository = ApprovedClinicDocumentContextRepository(
        session,
        clinic_id=actor.clinic_id,
    )
    clinic_document_context = tuple(
        await retrieve_planned_clinic_context(
            clinic_repository,
            queries=plan_clinic_document_queries(facts),
            as_of_date=as_of_date,
        )
    )
    available_clinic_documents = await clinic_repository.list_available(as_of_date=as_of_date)
    clinic_document_readiness = assess_clinic_document_readiness(
        plan_clinic_document_expectations(facts),
        available_documents=available_clinic_documents,
        retrieved_fragments=clinic_document_context,
    )

    analogies = None
    if (os.getenv("AI_FIRST_REFERENCE_ANALOGIES_ENABLED", "0") == "1"
            and case.created_by_membership_id == actor.membership_id):
        selected = await select_reference_analogies(session, actor=actor, facts=facts)
        if selected.examples:
            analogies = selected
    return AnalysisState(
        case=case,
        facts=facts,
        as_of_date=as_of_date,
        evidence=evidence,
        clinic_document_context=clinic_document_context,
        clinic_document_readiness=clinic_document_readiness,
        fact_snapshot_sha256=fact_snapshot_sha256(facts),
        evidence_trace_sha256=evidence_trace_sha256(evidence, as_of_date=as_of_date),
        clinic_document_context_trace_sha256=clinic_document_context_trace_sha256(
            clinic_document_context,
            as_of_date=as_of_date,
        ),
        risk_policy=policy,
        reference_analogies=analogies,
        case_material_context=await load_case_material_context(
            session, clinic_id=actor.clinic_id, case_id=case.id, facts=facts,
        ),
    )


def _context_fingerprint(state: AnalysisState) -> AnalysisContextFingerprint:
    return AnalysisContextFingerprint(
        as_of_date=state.as_of_date,
        fact_snapshot_sha256=state.fact_snapshot_sha256,
        evidence_trace_sha256=state.evidence_trace_sha256,
        clinic_document_context_trace_sha256=state.clinic_document_context_trace_sha256,
        risk_policy_version=state.risk_policy.domain.version,
        case_material_context_trace_sha256=state.case_material_context_trace_sha256,
    )


def _readiness_responses(
    readiness: tuple[ClinicDocumentReadiness, ...],
) -> list[ClinicDocumentReadinessResponse]:
    return [
        ClinicDocumentReadinessResponse(
            expectationCode=item.expectation_code,
            importance=item.importance.value,
            acceptedDocumentTypes=list(item.accepted_document_types),
            reasonCode=item.reason_code,
            status=item.status.value,
            matchedDocumentKeys=list(item.matched_document_keys),
            analysisBlocking=False,
        )
        for item in readiness
    ]


def _readiness_cards(
    readiness: tuple[ClinicDocumentReadiness, ...],
) -> list[ClinicDocumentReadinessCard]:
    return [
        ClinicDocumentReadinessCard.model_validate(
            item.model_dump(mode="json", by_alias=True)
        )
        for item in _readiness_responses(readiness)
    ]


def _context_response(state: AnalysisState) -> AnalysisContextResponse:
    return AnalysisContextResponse(
        caseId=state.case.id,
        asOfDate=state.as_of_date,
        facts={key.value: value for key, value in state.facts.items()},
        factSnapshotSha256=state.fact_snapshot_sha256,
        evidenceTraceSha256=state.evidence_trace_sha256,
        evidence=[
            LegalFragmentResponse.model_validate(fragment, from_attributes=True)
            for fragment in state.evidence
        ],
        clinicDocumentContextTraceSha256=state.clinic_document_context_trace_sha256,
        clinicDocumentContext=[
            ClinicDocumentContextResponse(
                fragmentId=fragment.fragment_id,
                versionId=fragment.version_id,
                documentId=fragment.document_id,
                documentKey=fragment.document_key,
                documentType=fragment.document_type,
                documentTitle=fragment.document_title,
                versionNo=fragment.version_no,
                validFrom=fragment.valid_from,
                validTo=fragment.valid_to,
                structuralPath=fragment.structural_path,
                text=fragment.fragment_text,
                textSha256=fragment.text_sha256,
                rawSha256=fragment.raw_sha256,
            )
            for fragment in state.clinic_document_context
        ],
        clinicDocumentReadiness=_readiness_responses(state.clinic_document_readiness),
        riskPolicyVersion=state.risk_policy.domain.version,
        highDemandThresholdKopecks=state.risk_policy.domain.high_demand_threshold_kopecks,
        referenceExamples=state.reference_analogies.examples if state.reference_analogies else None,
        referenceExampleTraceSha256=state.reference_example_trace_sha256,
        caseMaterialContext=list(state.case_material_context.fragments) or None,
        caseMaterialContextTraceSha256=state.case_material_context_trace_sha256,
    )


def _domain_claims(payload: AnalysisSubmissionRequest) -> tuple[ProposedClaim, ...]:
    return tuple(
        ProposedClaim(
            claim_id=item.claim_id,
            kind=ClaimKind(item.kind),
            text=item.text,
            evidence_fragment_ids=tuple(item.evidence_fragment_ids),
            required_fact_keys=tuple(item.required_fact_keys),
        )
        for item in payload.claims
    )


def _semantic_reviews(payload: AnalysisSubmissionRequest) -> tuple[SemanticReview, ...]:
    return tuple(
        SemanticReview(
            claim_id=item.claim_id,
            verdict=SemanticVerdict(item.verdict),
            reviewed_fragment_ids=tuple(item.reviewed_fragment_ids),
        )
        for item in payload.semantic_reviews
    )


def _verified_action_items(
    claims: tuple[ProposedClaim, ...],
    results: dict[str, VerificationResult],
) -> list[str]:
    return [
        claim.text
        for claim in claims
        if claim.kind is ClaimKind.ACTION
        and results.get(claim.claim_id) is VerificationResult.VERIFIED
    ]


async def _store_report(
    session: AsyncSession,
    *,
    actor: Any,
    case: Case,
    canonical: CanonicalReport,
) -> ReportResponse:
    report_json = canonical.model_dump(mode="json", by_alias=True)
    pdf = render_report_pdf(canonical)
    row = CaseReport(
        id=canonical.report_id,
        clinic_id=actor.clinic_id,
        case_id=case.id,
        report_version=canonical.report_version,
        schema_version=canonical.schema_version,
        status=case.status,
        report_json=report_json,
        content_sha256=_canonical_hash(report_json),
        pdf_bytes=pdf,
        pdf_sha256=hashlib.sha256(pdf).hexdigest(),
        pdf_size_bytes=len(pdf),
        facts_snapshot_sha256=canonical.fact_snapshot_sha256,
        created_by_membership_id=actor.membership_id,
    )
    session.add(row)
    await session.flush()
    return ReportResponse(
        id=row.id,
        caseId=row.case_id,
        reportVersion=row.report_version,
        reportJson=row.report_json,
        pdfSha256=row.pdf_sha256,
        createdAt=row.created_at,
    )


def create_analysis_router(
    session_factory: async_sessionmaker[AsyncSession],
) -> APIRouter:
    router = APIRouter(prefix="/v1/cases", tags=["case-analysis"])

    async def get_session() -> Any:
        async with session_factory() as session:
            yield session

    @router.get(
        "/{case_id}/analysis-context",
        response_model=AnalysisContextResponse,
        dependencies=[Depends(_require_agent_internal_key)],
    )
    async def get_analysis_context(
        case_id: UUID,
        telegram_user_id: TelegramUserId,
        session: AsyncSession = Depends(get_session),
    ) -> AnalysisContextResponse:
        actor = await resolve_actor(session, telegram_user_id)
        return _context_response(await _load_analysis_state(session, actor, case_id))

    async def submission_case(
        case_id: UUID,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: AsyncSession,
        x_analysis_job_id: UUID | None,
        x_analysis_job_token: UUID | None,
        result_version: int,
    ) -> tuple[ActorContext, Case]:
        actor = await resolve_actor(session, telegram_user_id)
        # A successful idempotency record is not an exemption from case retention.
        # Do not require analysis eligibility here: a valid completed case must still replay.
        owned_case = await _tenant_case(session, actor, case_id)
        if result_version == 2:
            _require_case_intake_actor(actor)
            if owned_case.created_by_membership_id != actor.membership_id:
                raise ApiError(status_code=404, code="CASE_NOT_FOUND", message="Case not found")
        # Serialize replay, eligibility and writes on a freshly loaded case. The case
        # lock precedes the job lock; no external/model I/O occurs in this transaction.
        owned_case = await _tenant_case(session, actor, case_id, for_update=True)
        if x_analysis_job_id is not None or x_analysis_job_token is not None:
            from legal_core.analysis_jobs import require_job_lease
            if (x_analysis_job_id is None or x_analysis_job_token is None
                    or idempotency_key != x_analysis_job_id):
                raise ApiError(status_code=409, code="ANALYSIS_JOB_LEASE_EXPIRED",
                               message="Invalid analysis job lease")
            job = await require_job_lease(session, actor, case_id,
                                          x_analysis_job_id, x_analysis_job_token)
            if job.result_version != result_version:
                raise ApiError(status_code=409, code="ANALYSIS_RESULT_VERSION_MISMATCH",
                               message="Analysis job result version mismatch")
        await recheck_submission_access(
            session, actor=actor, telegram_user_id=telegram_user_id, case=owned_case,
        )
        return actor, owned_case

    @router.post(
        "/{case_id}/preparation-context",
        response_model=AnalysisContextResponse | PreparationResult,
        dependencies=[Depends(_require_agent_internal_key)],
    )
    async def prepare_analysis_context(
        case_id: UUID,
        response: Response,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: AsyncSession = Depends(get_session),
        x_analysis_job_id: Annotated[UUID | None, Header()] = None,
        x_analysis_job_token: Annotated[UUID | None, Header()] = None,
    ) -> AnalysisContextResponse | PreparationResult:
        actor, case = await submission_case(
            case_id, telegram_user_id, idempotency_key, session,
            x_analysis_job_id, x_analysis_job_token, 2,
        )
        from legal_core.analysis_jobs import analysis_result_scope
        scope = analysis_result_scope(case_id, 2)
        request_hash = _canonical_hash({
            "operation": "PREPARE_ANALYSIS_CONTEXT", "resultVersion": 2,
        })
        # This preflight has no caller-supplied reasoning payload. A retry of
        # the whole analysis may already have committed its late result under
        # the same intent key; read that audited result rather than comparing
        # its late-payload hash to the preflight marker or calling models again.
        retained = await session.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.clinic_id == actor.clinic_id,
            IdempotencyRecord.actor_membership_id == actor.membership_id,
            IdempotencyRecord.scope == scope, IdempotencyRecord.key == str(idempotency_key),
            IdempotencyRecord.state == "SUCCEEDED",
        ))
        if retained is not None and retained.response_json is not None:
            return await read_preparation_replay(
                session, clinic_id=actor.clinic_id, actor_membership_id=actor.membership_id,
                case=case, scope=scope, idempotency_key=idempotency_key,
                wire=retained.response_json,
                require_current_case_materials=True,
            )
        replay = await _idempotency_replay(
            session, actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash,
        )
        if replay is not None:
            return await read_preparation_replay(
                session, clinic_id=actor.clinic_id, actor_membership_id=actor.membership_id,
                case=case, scope=scope, idempotency_key=idempotency_key, wire=replay,
                require_current_case_materials=True,
            )
        _require_analysis_eligible_case(case)
        require_analysis_running()
        facts = _domain_facts(await _current_fact_rows(session, case.id))
        try:
            policy = await ApprovedRiskPolicyRepository(session).get()
        except (LookupError, ValueError) as error:
            raise ApiError(status_code=409, code="RISK_POLICY_NOT_READY",
                           message="An approved risk policy is not available") from error
        early = (
            evaluate_early_triage(facts, policy=policy.domain)
            if policy.early_triage_enabled else None
        )
        source = PreparationSource(operationId=uuid4(), caseId=case.id,
                                   factSnapshotSha256=fact_snapshot_sha256(facts), context=None)
        if early is not None:
            risk_record = await record_case_risk_assessment(
                session, clinic_id=actor.clinic_id, case_id=case.id,
                actor_membership_id=actor.membership_id, policy_id=policy.id,
                assessment=early,
                evidence_trace_sha256=hashlib.sha256(b"EARLY_TRIAGE_NO_LEGAL_EVIDENCE").hexdigest(),
            )
            if risk_record.escalation_id is None:
                raise ApiError(status_code=500, code="PREPARATION_ESCALATION_MISSING",
                               message="Required escalation is unavailable")
            prepared = PreparationResult(HumanReviewResult(
                resultVersion=2, status="HUMAN_REVIEW", source=source,
                escalationId=risk_record.escalation_id,
                riskLevel="CRITICAL" if early.level == RiskLevel.CRITICAL else "HIGH",
                riskPolicyVersion=early.policy_version,
            ))
            case.status = CaseStatus.ESCALATION_REQUIRED.value
            case.updated_at = datetime.now(UTC)
        else:
            try:
                return _context_response(await _load_analysis_state(session, actor, case_id))
            except ApiError as error:
                prepared = precontext_limitation(error=error, source=source, facts=facts)
        await store_preparation_result(
            session, actor=actor, case=case, scope=scope, idempotency_key=idempotency_key,
            request_hash=request_hash, result=prepared,
        )
        await session.commit()
        response.status_code = status.HTTP_201_CREATED
        return prepared

    async def submit_result(
        case_id: UUID,
        payload: AnalysisSubmissionRequest,
        response: Response,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: AsyncSession,
        x_analysis_job_id: UUID | None,
        x_analysis_job_token: UUID | None,
        result_version: int,
    ) -> AnalysisSubmissionResponse | PreparationResult:
        actor, owned_case = await submission_case(
            case_id, telegram_user_id, idempotency_key, session,
            x_analysis_job_id, x_analysis_job_token, result_version,
        )
        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        from legal_core.analysis_jobs import analysis_result_scope
        scope = analysis_result_scope(case_id, result_version)
        replay = await _idempotency_replay(
            session,
            actor=actor,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = status.HTTP_200_OK
            if result_version == 2:
                return await read_preparation_replay(
                    session, clinic_id=actor.clinic_id, actor_membership_id=actor.membership_id,
                    case=owned_case, scope=scope,
                    idempotency_key=idempotency_key, wire=replay,
                )
            return AnalysisSubmissionResponse.model_validate(replay)

        state = await _load_analysis_state(session, actor, case_id)
        expected_fingerprint = AnalysisContextFingerprint(
            as_of_date=payload.as_of_date,
            fact_snapshot_sha256=payload.expected_fact_snapshot_sha256,
            evidence_trace_sha256=payload.expected_evidence_trace_sha256,
            clinic_document_context_trace_sha256=(
                payload.expected_clinic_document_context_trace_sha256
            ),
            risk_policy_version=payload.expected_risk_policy_version,
            case_material_context_trace_sha256=payload.expected_case_material_context_trace_sha256,
        )
        if (analysis_context_is_stale(expected_fingerprint, _context_fingerprint(state))
                or payload.expected_reference_example_trace_sha256
                != state.reference_example_trace_sha256):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="ANALYSIS_CONTEXT_STALE",
                message=(
                    "Case facts, evidence, clinic context or risk policy changed "
                    "during analysis"
                ),
            )

        await require_reference_analogy_trace_current(
            session, case=state.case, facts=state.facts,
            expected_trace=state.reference_example_trace_sha256,
        )

        try:
            clarification_requests = validate_request_scope(
                payload.clarification_requests,
                {key.value: value for key, value in state.facts.items()},
            )
        except ValueError as exc:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="CLARIFICATION_REQUEST_NOT_APPLICABLE",
                message="Clarification does not match unanswered frozen case facts",
            ) from exc

        claims = _domain_claims(payload)
        reviews = _semantic_reviews(payload)
        outcome = analyze_frozen_case(
            facts=state.facts,
            as_of_date=state.as_of_date,
            evidence=state.evidence,
            claims=claims,
            semantic_reviews=reviews,
            risk_policy=state.risk_policy.domain,
        )
        clarification_requests = complete_service_date_requests(
            clarification_requests, {key.value: value for key, value in state.facts.items()},
            service_date_binding_unknown=any(
                claim.reason_code == "SERVICE_DATE_BINDING_UNKNOWN"
                for claim in outcome.verification.claims
            ),
        )
        # Even verified partial claims cannot make an incomplete factual analysis READY.
        # Pending questions are not confirmed facts, legal evidence or recommendations.
        analysis_allowed = outcome.analysis_allowed and not clarification_requests

        risk_record = await record_case_risk_assessment(
            session,
            clinic_id=actor.clinic_id,
            case_id=state.case.id,
            actor_membership_id=actor.membership_id,
            policy_id=state.risk_policy.id,
            assessment=outcome.risk,
            evidence_trace_sha256=outcome.evidence_trace_sha256,
        )
        verifier_payload = build_verifier_run_payload(claims, outcome.verification)
        verifier_record = await record_verifier_run(
            session,
            clinic_id=actor.clinic_id,
            case_id=state.case.id,
            actor_membership_id=actor.membership_id,
            case_risk_assessment_id=risk_record.assessment_id,
            as_of_date=state.as_of_date,
            fact_snapshot_sha256=outcome.risk.fact_snapshot_sha256,
            evidence_trace_sha256=outcome.evidence_trace_sha256,
            payload=verifier_payload,
        )

        if analysis_allowed:
            state.case.status = (
                CaseStatus.ESCALATION_REQUIRED.value
                if outcome.risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
                else CaseStatus.REPORT_READY.value
            )
        else:
            state.case.status = CaseStatus.ANALYSIS_BLOCKED.value
        state.case.updated_at = datetime.now(UTC)

        source = PreparationSource(
            operationId=verifier_record.analysis_run_id, caseId=state.case.id,
            factSnapshotSha256=state.fact_snapshot_sha256,
            context=PreparationContext(
                asOfDate=state.as_of_date, evidenceTraceSha256=state.evidence_trace_sha256,
                clinicDocumentContextTraceSha256=state.clinic_document_context_trace_sha256,
                riskPolicyVersion=state.risk_policy.domain.version,
                referenceExampleTraceSha256=state.reference_example_trace_sha256,
                caseMaterialContextTraceSha256=state.case_material_context_trace_sha256,
            ),
        ) if result_version == 2 else None
        if source is not None:
            preparation = intermediate_result(
                source=source, risk=outcome.risk, escalation_id=risk_record.escalation_id,
                clarification_requests=clarification_requests, analysis_allowed=analysis_allowed,
            )
            if preparation is not None:
                if preparation.root.status == "HUMAN_REVIEW":
                    state.case.status = CaseStatus.ESCALATION_REQUIRED.value
                await store_preparation_result(
                    session, actor=actor, case=state.case, scope=scope,
                    idempotency_key=idempotency_key, request_hash=request_hash, result=preparation,
                )
                await _commit_analysis_result(session, state)
                return preparation

        current_version = await session.scalar(
            select(func.coalesce(func.max(CaseReport.report_version), 0)).where(
                CaseReport.case_id == state.case.id
            )
        )
        report_version = int(current_version or 0) + 1
        report_id = uuid4()
        if analysis_allowed:
            result_by_claim = {
                item.claim_id: item.result for item in outcome.verification.claims
            }
            canonical = build_analysis_report(
                report_id=report_id,
                analysis_run_id=verifier_record.analysis_run_id,
                case_id=state.case.id,
                public_number=_case_response(state.case).public_number,
                case_status=CaseStatus(state.case.status),
                report_version=report_version,
                generated_at=datetime.now(UTC),
                as_of_date=state.as_of_date,
                facts=state.facts,
                missing_facts=[],
                risk=outcome.risk,
                evidence_trace_sha256=outcome.evidence_trace_sha256,
                evidence=state.evidence,
                verification=outcome.verification,
                clinic_document_context_trace_sha256=(
                    state.clinic_document_context_trace_sha256
                ),
                clinic_document_context=state.clinic_document_context,
                case_material_context=state.case_material_context.fragments,
                case_material_context_trace_sha256=state.case_material_context_trace_sha256,
                verified_action_items=_verified_action_items(claims, result_by_claim),
                verified_legal_conclusions=select_verified_legal_conclusions(
                    claims, outcome.verification
                ),
            )
        else:
            block_reason = (
                "FACTUAL_CLARIFICATION_REQUIRED" if clarification_requests else
                verifier_payload.block_reason_codes[0]
                if verifier_payload.block_reason_codes
                else outcome.risk.reason_codes[0]
            )
            canonical = build_intake_report(
                report_id=report_id,
                case_id=state.case.id,
                public_number=_case_response(state.case).public_number,
                case_status=CaseStatus.ANALYSIS_BLOCKED,
                report_version=report_version,
                generated_at=datetime.now(UTC),
                facts=state.facts,
                missing_facts=risk_missing_facts(outcome.risk),
                block_reason_code=block_reason,
                clarification_requests=clarification_requests,
                case_material_context_trace_sha256=state.case_material_context_trace_sha256,
            )

        canonical = canonical.model_copy(
            update={
                "clinic_document_readiness": _readiness_cards(
                    state.clinic_document_readiness
                )
            }
        )
        report = await _store_report(
            session,
            actor=actor,
            case=state.case,
            canonical=canonical,
        )
        if source is not None:
            ready = PreparationResult(ReportReadyResult(
                resultVersion=2, status="READY", source=source, analysisAllowed=True, report=report,
            ))
            await store_preparation_result(
                session, actor=actor, case=state.case, scope=scope,
                idempotency_key=idempotency_key, request_hash=request_hash, result=ready,
            )
            await _commit_analysis_result(session, state)
            return ready
        idempotency = _new_idempotency_record(
            actor=actor,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        session.add(idempotency)
        response_payload = AnalysisSubmissionResponse(
            analysisAllowed=analysis_allowed,
            riskLevel=outcome.risk.level.value,
            escalationRequired=(
                outcome.risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            ),
            escalationId=risk_record.escalation_id,
            clinicDocumentReadiness=_readiness_responses(state.clinic_document_readiness),
            report=report,
        )
        _finish_idempotency(
            idempotency,
            resource_type="CASE_ANALYSIS_RUN",
            resource_id=verifier_record.analysis_run_id,
            response_json=response_payload.model_dump(mode="json", by_alias=True),
        )
        missing_document_count = sum(
            item.status is ClinicDocumentReadinessStatus.NOT_AVAILABLE
            for item in state.clinic_document_readiness
        )
        session.add(
            _audit(
                actor=actor,
                action="CASE_ANALYSIS_COMPLETED",
                resource_type="CASE_ANALYSIS_RUN",
                resource_id=verifier_record.analysis_run_id,
                metadata={
                    "analysisAllowed": analysis_allowed,
                    "reportId": str(report.id),
                    "riskLevel": outcome.risk.level.value,
                    "clinicDocumentContextCount": len(state.clinic_document_context),
                    "clinicDocumentContextTraceSha256": (
                        state.clinic_document_context_trace_sha256
                    ),
                    "clinicDocumentExpectationCount": len(state.clinic_document_readiness),
                    "clinicDocumentNotAvailableCount": missing_document_count,
                    "draftPolicyVersion": canonical.draft_response.policy_version,
                    "clarificationRequestCount": len(clarification_requests),
                    "clarificationQuestionIds": [
                        request.question_id.value for request in clarification_requests
                    ],
                    **({"referenceExampleTraceSha256": state.reference_example_trace_sha256}
                       if state.reference_example_trace_sha256 is not None else {}),
                },
            )
        )
        await _commit_analysis_result(session, state)
        return response_payload

    @router.post(
        "/{case_id}/analysis-submissions", response_model=AnalysisSubmissionResponse,
        status_code=status.HTTP_201_CREATED, dependencies=[Depends(_require_agent_internal_key)],
    )
    async def submit_analysis(
        case_id: UUID, payload: AnalysisSubmissionRequest, response: Response,
        telegram_user_id: TelegramUserId, idempotency_key: IdempotencyKey,
        session: AsyncSession = Depends(get_session),
        x_analysis_job_id: UUID | None = Header(default=None),
        x_analysis_job_token: UUID | None = Header(default=None),
    ) -> AnalysisSubmissionResponse:
        result = await submit_result(case_id, payload, response, telegram_user_id, idempotency_key,
                                     session, x_analysis_job_id, x_analysis_job_token, 1)
        assert isinstance(result, AnalysisSubmissionResponse)
        return result

    @router.post(
        "/{case_id}/preparation-submissions", response_model=PreparationResult,
        status_code=status.HTTP_201_CREATED, dependencies=[Depends(_require_agent_internal_key)],
    )
    async def submit_preparation(
        case_id: UUID, payload: AnalysisSubmissionRequest, response: Response,
        telegram_user_id: TelegramUserId, idempotency_key: IdempotencyKey,
        session: AsyncSession = Depends(get_session),
        x_analysis_job_id: UUID | None = Header(default=None),
        x_analysis_job_token: UUID | None = Header(default=None),
    ) -> PreparationResult:
        result = await submit_result(case_id, payload, response, telegram_user_id, idempotency_key,
                                     session, x_analysis_job_id, x_analysis_job_token, 2)
        assert isinstance(result, PreparationResult)
        return result

    return router
