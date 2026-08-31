"""Tenant-scoped REST boundary for trusted agent analysis submissions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis import analyze_frozen_case, evidence_trace_sha256
from legal_core.analysis_contracts import (
    AnalysisContextResponse,
    AnalysisSubmissionRequest,
    AnalysisSubmissionResponse,
)
from legal_core.api_contracts import LegalFragmentResponse, ReportResponse
from legal_core.case_api import (
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
    _tenant_case,
    resolve_actor,
)
from legal_core.contracts import CanonicalReport, CaseStatus, FactKey
from legal_core.intake import missing_facts_for
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository, ApprovedLegalFragment
from legal_core.models import Case, CaseReport
from legal_core.reports import build_analysis_report, build_intake_report, render_report_pdf
from legal_core.retrieval_plan import plan_legal_queries, retrieve_planned_evidence
from legal_core.risk_engine import RiskLevel, fact_snapshot_sha256
from legal_core.risk_persistence import record_case_risk_assessment
from legal_core.risk_policy_repository import ApprovedRiskPolicy, ApprovedRiskPolicyRepository
from legal_core.verifier import (
    ClaimKind,
    ProposedClaim,
    SemanticReview,
    SemanticVerdict,
    VerificationResult,
)
from legal_core.verifier_persistence import build_verifier_run_payload, record_verifier_run


@dataclass(frozen=True, slots=True)
class AnalysisState:
    case: Case
    facts: dict[FactKey, object]
    as_of_date: date
    evidence: tuple[ApprovedLegalFragment, ...]
    fact_snapshot_sha256: str
    evidence_trace_sha256: str
    risk_policy: ApprovedRiskPolicy


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
    for key in (
        FactKey.CLAIM_DATE,
        FactKey.INCIDENT_DATE,
        FactKey.SERVICE_DATE,
    ):
        resolved = _exact_date(facts.get(key))
        if resolved is not None:
            return resolved
    return datetime.now(UTC).date()


async def _load_analysis_state(session: AsyncSession, actor: Any, case_id: UUID) -> AnalysisState:
    case = await _tenant_case(session, actor, case_id)
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

    return AnalysisState(
        case=case,
        facts=facts,
        as_of_date=as_of_date,
        evidence=evidence,
        fact_snapshot_sha256=fact_snapshot_sha256(facts),
        evidence_trace_sha256=evidence_trace_sha256(evidence, as_of_date=as_of_date),
        risk_policy=policy,
    )


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
        riskPolicyVersion=state.risk_policy.domain.version,
        highDemandThresholdKopecks=state.risk_policy.domain.high_demand_threshold_kopecks,
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

    Session = Annotated[AsyncSession, Depends(get_session)]

    @router.get("/{case_id}/analysis-context", response_model=AnalysisContextResponse)
    async def get_analysis_context(
        case_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> AnalysisContextResponse:
        actor = await resolve_actor(session, telegram_user_id)
        return _context_response(await _load_analysis_state(session, actor, case_id))

    @router.post(
        "/{case_id}/analysis-submissions",
        response_model=AnalysisSubmissionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def submit_analysis(
        case_id: UUID,
        payload: AnalysisSubmissionRequest,
        response: Response,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> AnalysisSubmissionResponse:
        actor = await resolve_actor(session, telegram_user_id)
        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        scope = f"cases:{case_id}:analysis-submissions"
        replay = await _idempotency_replay(
            session,
            actor=actor,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = status.HTTP_200_OK
            return AnalysisSubmissionResponse.model_validate(replay)

        state = await _load_analysis_state(session, actor, case_id)
        stale = (
            payload.as_of_date != state.as_of_date
            or payload.expected_fact_snapshot_sha256 != state.fact_snapshot_sha256
            or payload.expected_evidence_trace_sha256 != state.evidence_trace_sha256
            or payload.expected_risk_policy_version != state.risk_policy.domain.version
        )
        if stale:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="ANALYSIS_CONTEXT_STALE",
                message="Case facts, evidence or risk policy changed during analysis",
            )

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

        if outcome.analysis_allowed:
            state.case.status = (
                CaseStatus.ESCALATION_REQUIRED.value
                if outcome.risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
                else CaseStatus.REPORT_READY.value
            )
        else:
            state.case.status = CaseStatus.ANALYSIS_BLOCKED.value
        state.case.updated_at = datetime.now(UTC)

        current_version = await session.scalar(
            select(func.coalesce(func.max(CaseReport.report_version), 0)).where(
                CaseReport.case_id == state.case.id
            )
        )
        report_version = int(current_version or 0) + 1
        report_id = uuid4()
        if outcome.analysis_allowed:
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
                verified_action_items=_verified_action_items(claims, result_by_claim),
            )
        else:
            block_reason = (
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
                missing_facts=[],
                block_reason_code=block_reason,
            )

        report = await _store_report(
            session,
            actor=actor,
            case=state.case,
            canonical=canonical,
        )
        idempotency = _new_idempotency_record(
            actor=actor,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        session.add(idempotency)
        response_payload = AnalysisSubmissionResponse(
            analysisAllowed=outcome.analysis_allowed,
            riskLevel=outcome.risk.level.value,
            escalationRequired=(
                outcome.risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            ),
            report=report,
        )
        _finish_idempotency(
            idempotency,
            resource_type="CASE_ANALYSIS_RUN",
            resource_id=verifier_record.analysis_run_id,
            response_json=response_payload.model_dump(mode="json", by_alias=True),
        )
        session.add(
            _audit(
                actor=actor,
                action="CASE_ANALYSIS_COMPLETED",
                resource_type="CASE_ANALYSIS_RUN",
                resource_id=verifier_record.analysis_run_id,
                metadata={
                    "analysisAllowed": outcome.analysis_allowed,
                    "reportId": str(report.id),
                    "riskLevel": outcome.risk.level.value,
                },
            )
        )
        await session.commit()
        return response_payload

    return router
