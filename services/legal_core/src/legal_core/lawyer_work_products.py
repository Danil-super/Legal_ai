"""Verified internal drafts and append-only decisions by the assigned human lawyer.

This boundary never creates a patient report, changes risk, or resolves a case.
All writers acquire Case before the version table (also the retention lock order).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis_api import (
    AnalysisState,
    _analysis_date,
    _require_agent_internal_key,
    _retrieve_analysis_state,
)
from legal_core.analysis_jobs import require_job_lease
from legal_core.analysis_safe_stop import require_analysis_running
from legal_core.case_api import (
    ActorContext,
    ApiError,
    IdempotencyKey,
    TelegramUserId,
    _audit,
    _canonical_hash,
    _current_fact_rows,
    _discussion_escalation,
    _domain_facts,
    _escalation_state,
    _finish_idempotency,
    _idempotency_replay,
    _new_idempotency_record,
    _tenant_case,
    resolve_actor,
)
from legal_core.case_material_context import (
    CaseMaterialContextState,
    require_case_material_context_current,
)
from legal_core.case_preparation import recheck_submission_access
from legal_core.case_preparation_contracts import InternalWorkProduct
from legal_core.contracts import FactKey, LegalFragmentResponse
from legal_core.lawyer_draft_context import (
    _policy,
    _require_enabled,
    load_lawyer_draft_context,
)
from legal_core.lawyer_draft_contracts import LawyerDraftContextRequest
from legal_core.lawyer_draft_freshness import require_lawyer_draft_sources_current
from legal_core.lawyer_work_product_contracts import (
    LawyerWorkProductReview,
    LawyerWorkProductSubmission,
    LawyerWorkProductView,
)
from legal_core.models import AuditEvent, Case, CaseEscalation
from legal_core.reference_analogy_freshness import require_reference_analogy_trace_current
from legal_core.risk_engine import fact_snapshot_sha256
from legal_core.verifier import (
    ClaimKind,
    ProposedClaim,
    SemanticReview,
    SemanticVerdict,
    verify_claims,
)
from legal_core.verifier_persistence import build_verifier_run_payload, record_verifier_run
from legal_core.work_product_storage import CaseWorkProductVersion


def _missing() -> ApiError:
    return ApiError(status_code=404, code="LAWYER_WORK_PRODUCT_NOT_FOUND",
                    message="Work product not found")


def _stale() -> ApiError:
    return ApiError(status_code=409, code="LAWYER_WORK_PRODUCT_STALE",
                    message="Open the current case plan before reviewing it")


def _uncertainties(facts: dict[FactKey, object]) -> list[str]:
    """State bounded factual gaps, without inventing answers or legal conclusions."""
    result: list[str] = []
    screening = facts.get(FactKey.FACTUAL_SAFETY_SCREENING)
    if isinstance(screening, dict):
        labels = {
            "hospitalizationReported": "Госпитализация: сведения отсутствуют.",
            "healthDeteriorationReported": "Ухудшение здоровья: сведения отсутствуют.",
            "authorityOrCourtDocumentReceived": "Документ суда или органа: получение неизвестно.",
            "moneyRequested": "Денежное требование: наличие неизвестно.",
        }
        result.extend(text for key, text in labels.items() if screening.get(key) == "UNKNOWN")
        if screening.get("moneyRequested") == "YES" and screening.get("amount") == "UNKNOWN":
            result.append("Сумма денежного требования неизвестна.")
    services = facts.get(FactKey.AFFECTED_SERVICES)
    answers = facts.get(FactKey.ANALYSIS_CLARIFICATION_ANSWERS)
    by_identity = {
        (item.get("questionId"), item.get("serviceSlot")): item.get("value")
        for item in answers.get("answers", []) if isinstance(item, dict)
    } if isinstance(answers, dict) else {}
    if isinstance(services, list):
        for index, service in enumerate(services):
            provided = by_identity.get(("SERVICE_PROVIDED_DATE", index))
            if not isinstance(provided, dict) or provided.get("precision") != "EXACT":
                result.append(f"Точная дата оказания услуги «{service}» не установлена.")
            if by_identity.get(("SERVICE_PAYMENT_BASIS", index)) in {None, "UNKNOWN"}:
                result.append(f"Основание оплаты услуги «{service}» не уточнено.")
    return result[:5]


async def _latest(session: AsyncSession, actor: ActorContext,
                   case_id: UUID) -> CaseWorkProductVersion | None:
    row: CaseWorkProductVersion | None = await session.scalar(select(CaseWorkProductVersion).where(
        CaseWorkProductVersion.clinic_id == actor.clinic_id,
        CaseWorkProductVersion.case_id == case_id,
    ).order_by(CaseWorkProductVersion.version.desc(), CaseWorkProductVersion.created_at.desc(),
               CaseWorkProductVersion.id.desc()).limit(1))
    return row


def _new_version(actor: ActorContext, content: InternalWorkProduct, *, origin: str,
                 review_state: str) -> CaseWorkProductVersion:
    context = content.source.context
    assert context is not None  # All writers validate the typed source first.
    approved = review_state == "HUMAN_APPROVED"
    return CaseWorkProductVersion(
        id=uuid4(), clinic_id=actor.clinic_id, case_id=content.source.case_id,
        work_product_id=content.work_product_id, version=content.version,
        parent_version=content.parent_version, origin=origin, review_state=review_state,
        author_membership_id=actor.membership_id,
        reviewed_by_membership_id=actor.membership_id if approved else None,
        reviewed_at=datetime.now(UTC) if approved else None,
        created_at=datetime.now(UTC),
        schema_version=content.schema_version,
        payload_json=content.model_dump(mode="json", by_alias=True),
        content_sha256=content.content_sha256, source_operation_id=content.source.operation_id,
        fact_snapshot_sha256=content.source.fact_snapshot_sha256,
        evidence_trace_sha256=context.evidence_trace_sha256,
        clinic_document_context_trace_sha256=context.clinic_document_context_trace_sha256,
        reference_example_trace_sha256=context.reference_example_trace_sha256,
        risk_policy_version=content.risk_policy_version, as_of_date=content.as_of_date,
    )


async def _view(session: AsyncSession, *, actor: ActorContext, case: Case,
                row: CaseWorkProductVersion,
                loaded_state: AnalysisState | None = None,
                material_contexts: list[CaseMaterialContextState] | None = None,
                ) -> LawyerWorkProductView:
    content = InternalWorkProduct.model_validate(row.payload_json)
    reference_trace = (content.source.context.reference_example_trace_sha256
                       if content.source.context else None)
    if (content.content_sha256 != row.content_sha256
            or row.reference_example_trace_sha256 != reference_trace):
        raise _stale()
    latest = await _latest(session, actor, case.id)
    current = (latest is not None and latest.id == row.id
               and case.status == "ESCALATION_REQUIRED")
    citations: list[LegalFragmentResponse] = []
    facts = _domain_facts(await _current_fact_rows(session, case.id))
    if fact_snapshot_sha256(facts) != content.source.fact_snapshot_sha256:
        current = False
    if current:
        try:
            if loaded_state is None:
                policy = await _policy(session)
                if policy.domain.version != content.risk_policy_version:
                    raise _stale()
                state = await _retrieve_analysis_state(
                    session, actor=actor, case=case, facts=facts,
                    as_of_date=_analysis_date(facts), policy=policy,
                )
                await require_lawyer_draft_sources_current(session, actor=actor, state=state)
                await require_reference_analogy_trace_current(
                    session, case=case, facts=facts, expected_trace=reference_trace,
                )
            else:
                # The private loader already fenced this exact state. Repeating
                # retrieval here could invoke embeddings while source locks are held.
                state = loaded_state
                if (state.case.id != case.id or state.case.clinic_id != actor.clinic_id
                        or state.fact_snapshot_sha256 != content.source.fact_snapshot_sha256
                        or state.risk_policy.domain.version != content.risk_policy_version
                        or state.reference_example_trace_sha256 != reference_trace):
                    raise _stale()
            bound = content.source.context
            if (bound is None or state.as_of_date != bound.as_of_date
                    or state.evidence_trace_sha256 != bound.evidence_trace_sha256
                    or state.clinic_document_context_trace_sha256
                    != bound.clinic_document_context_trace_sha256
                    or state.case_material_context_trace_sha256
                    != bound.case_material_context_trace_sha256):
                raise _stale()
            if material_contexts is not None:
                # Let mutations recheck natural expiry after subsequent FK/auth
                # waits without repeating private-object I/O under those locks.
                material_contexts.append(state.case_material_context)
            by_id = {fragment.fragment_id: fragment for fragment in state.evidence}
            citations = [LegalFragmentResponse.model_validate(by_id[identifier],
                                                              from_attributes=True)
                         for identifier in content.legal_fragment_ids if identifier in by_id]
            if len(citations) != len(content.legal_fragment_ids):
                raise _stale()
        except ApiError as error:
            if error.code not in {"LAWYER_WORK_PRODUCT_STALE", "RISK_POLICY_NOT_READY",
                                  "ANALYSIS_DATE_UNCERTAIN", "LEGAL_EVIDENCE_UNAVAILABLE",
                                  "LAWYER_DRAFT_CONTEXT_STALE", "REFERENCE_ANALOGY_CONTEXT_STALE",
                                  "CASE_MATERIAL_CONTEXT_UNAVAILABLE", "ANALYSIS_CONTEXT_STALE"}:
                raise
            current, citations = False, []
    return LawyerWorkProductView.model_validate({
        "versionId": row.id, "reviewState": row.review_state, "origin": row.origin,
        "isCurrent": current, "contentSha256": row.content_sha256,
        "content": content, "citations": citations,
    })


async def _receipt_row(session: AsyncSession, *, actor: ActorContext, case: Case,
                       scope: str, key: UUID, request_hash: str) -> CaseWorkProductVersion | None:
    replay = await _idempotency_replay(session, actor=actor, scope=scope, key=key,
                                      request_hash=request_hash)
    if replay is None:
        return None
    try:
        version_id = UUID(replay["versionId"])
    except (ValueError, KeyError, TypeError):
        raise _stale() from None
    row = await session.scalar(select(CaseWorkProductVersion).where(
        CaseWorkProductVersion.id == version_id,
        CaseWorkProductVersion.clinic_id == actor.clinic_id,
        CaseWorkProductVersion.case_id == case.id,
    ))
    if row is None:
        raise _missing()
    audit = await session.scalar(select(AuditEvent.id).where(
        AuditEvent.clinic_id == actor.clinic_id,
        AuditEvent.actor_membership_id == actor.membership_id,
        AuditEvent.resource_type == "CASE_WORK_PRODUCT", AuditEvent.resource_id == row.id,
        AuditEvent.action.in_({"LAWYER_WORK_PRODUCT_CREATED", "LAWYER_WORK_PRODUCT_REVIEWED"}),
        AuditEvent.metadata_json["requestSha256"].as_string() == request_hash,
        AuditEvent.metadata_json["operationKey"].as_string() == str(key),
    ))
    if audit is None:
        raise _stale()
    return row


def _retain_receipt(session: AsyncSession, *, actor: ActorContext, row: CaseWorkProductVersion,
                    scope: str, key: UUID, request_hash: str) -> None:
    record = _new_idempotency_record(actor=actor, scope=scope, key=key, request_hash=request_hash)
    _finish_idempotency(record, resource_type="CASE_WORK_PRODUCT", resource_id=row.id,
                        response_json={"versionId": str(row.id)})
    session.add(record)


async def _private_finish(session: AsyncSession, *, actor: ActorContext, case: Case,
                           telegram_user_id: int, job_id: UUID | None,
                           job_token: UUID | None) -> None:
    if (job_id is None or job_token is None
            or case.created_by_membership_id != actor.membership_id):
        raise _stale()
    await recheck_submission_access(session, actor=actor, telegram_user_id=telegram_user_id,
                                    case=case)
    await require_job_lease(session, actor, case.id, job_id, job_token)
    _require_enabled()
    require_analysis_running()


async def _commit_with_reference_fence(
    session: AsyncSession, *, case: Case, reference_trace: str | None,
    material_context: CaseMaterialContextState | None = None,
) -> None:
    # FK checks and final authorization may wait after the first source fence.
    # Flush every pending version/audit/receipt before evaluating natural expiry.
    await session.flush()
    if reference_trace is not None:
        facts = _domain_facts(await _current_fact_rows(session, case.id))
        await require_reference_analogy_trace_current(
            session, case=case, facts=facts, expected_trace=reference_trace,
        )
    if material_context is not None:
        await require_case_material_context_current(
            session, clinic_id=case.clinic_id, case_id=case.id, state=material_context,
        )
    await session.commit()


async def _public_case(session: AsyncSession, *, telegram_user_id: int, escalation_id: UUID,
                        mutation: bool = False) -> tuple[ActorContext, Case, CaseEscalation]:
    _require_enabled()
    actor = await resolve_actor(session, telegram_user_id)
    escalation = await _discussion_escalation(session, actor, escalation_id)
    case = await _tenant_case(session, actor, escalation.case_id, for_update=True)
    locked_escalation = await session.scalar(select(CaseEscalation).where(
        CaseEscalation.id == escalation_id, CaseEscalation.clinic_id == actor.clinic_id,
        CaseEscalation.case_id == case.id,
    ).with_for_update().execution_options(populate_existing=True))
    if locked_escalation is None:
        raise _missing()
    await recheck_submission_access(session, actor=actor, telegram_user_id=telegram_user_id,
                                    case=case)
    status, assignee = await _escalation_state(session, actor, escalation_id)
    is_assigned = (actor.role in {"CLINIC_OWNER", "CLINIC_LAWYER"}
                   and assignee == actor.membership_id)
    if mutation:
        require_analysis_running()
        if not is_assigned or status != "IN_PROGRESS":
            raise ApiError(status_code=403, code="WORK_PRODUCT_REVIEW_NOT_ALLOWED",
                           message="Only the currently assigned lawyer may review this plan")
    elif actor.role != "CLINIC_OWNER" and not is_assigned:
        if (actor.role != "CLINIC_ADMIN"
                or case.created_by_membership_id != actor.membership_id):
            raise _missing()
    return actor, case, locked_escalation


def create_lawyer_work_product_router(sessions: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter(tags=["lawyer-work-products"])

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    Session = Annotated[AsyncSession, Depends(session_dependency)]

    @router.post("/v1/cases/{case_id}/lawyer-work-products/lookup",
                 response_model=LawyerWorkProductView | None,
                 dependencies=[Depends(_require_agent_internal_key)])
    async def lookup(
        case_id: UUID, payload: LawyerDraftContextRequest, telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey, session: Session,
        x_analysis_job_id: Annotated[UUID | None, Header()] = None,
        x_analysis_job_token: Annotated[UUID | None, Header()] = None,
    ) -> LawyerWorkProductView | None:
        loaded = await load_lawyer_draft_context(
            session, case_id=case_id, telegram_user_id=telegram_user_id,
            idempotency_key=idempotency_key, job_id=x_analysis_job_id,
            job_token=x_analysis_job_token, request=payload,
        )
        row = await _latest(session, loaded.actor, case_id)
        result = None
        if row is not None and row.source_operation_id == payload.expected_preparation_operation_id:
            result = await _view(session, actor=loaded.actor,
                                  case=loaded.analysis_state.case, row=row,
                                  loaded_state=loaded.analysis_state)
        await _private_finish(session, actor=loaded.actor, case=loaded.analysis_state.case,
                               telegram_user_id=telegram_user_id, job_id=x_analysis_job_id,
                               job_token=x_analysis_job_token)
        await _commit_with_reference_fence(
            session, case=loaded.analysis_state.case,
            reference_trace=loaded.analysis_state.reference_example_trace_sha256,
            material_context=loaded.analysis_state.case_material_context,
        )
        return result

    @router.post("/v1/cases/{case_id}/lawyer-work-products",
                 response_model=LawyerWorkProductView, status_code=201,
                 dependencies=[Depends(_require_agent_internal_key)])
    async def submit(
        case_id: UUID, payload: LawyerWorkProductSubmission, telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey, response: Response, session: Session,
        x_analysis_job_id: Annotated[UUID | None, Header()] = None,
        x_analysis_job_token: Annotated[UUID | None, Header()] = None,
    ) -> LawyerWorkProductView:
        source = payload.expected_source
        assert source.context is not None
        loaded = await load_lawyer_draft_context(
            session, case_id=case_id, telegram_user_id=telegram_user_id,
            idempotency_key=idempotency_key, job_id=x_analysis_job_id,
            job_token=x_analysis_job_token, request=LawyerDraftContextRequest(
                expectedPreparationOperationId=source.operation_id,
                expectedFactSnapshotSha256=source.fact_snapshot_sha256,
                expectedRiskPolicyVersion=source.context.risk_policy_version,
            ),
        )
        if loaded.response.source != source:
            raise _stale()
        actor, state = loaded.actor, loaded.analysis_state
        scope, request_hash = f"lawyer-product:submit:{case_id}", _canonical_hash(
            payload.model_dump(mode="json", by_alias=True),
        )
        row = await _receipt_row(session, actor=actor, case=state.case, scope=scope,
                                  key=idempotency_key, request_hash=request_hash)
        if row is not None:
            if (row.origin != "AI" or row.author_membership_id != actor.membership_id
                    or row.payload_json.get("source")
                    != source.model_dump(mode="json", by_alias=True)):
                raise _stale()
            response.status_code = 200
            view = await _view(session, actor=actor, case=state.case, row=row, loaded_state=state)
            await _private_finish(session, actor=actor, case=state.case,
                                   telegram_user_id=telegram_user_id, job_id=x_analysis_job_id,
                                   job_token=x_analysis_job_token)
            await _commit_with_reference_fence(
                session, case=state.case,
                reference_trace=state.reference_example_trace_sha256,
                material_context=state.case_material_context,
            )
            return view
        previous = await _latest(session, actor, case_id)
        if previous is not None and previous.origin == "HUMAN":
            raise ApiError(status_code=409, code="LAWYER_WORK_PRODUCT_ALREADY_REVIEWED",
                           message="A human-reviewed plan cannot be replaced by an automatic draft")
        claims = tuple(ProposedClaim(
            claim_id=item.claim_id, kind=ClaimKind(item.kind), text=item.text,
            evidence_fragment_ids=tuple(item.evidence_fragment_ids),
            required_fact_keys=tuple(item.required_fact_keys),
        ) for item in payload.claims)
        reviews = tuple(SemanticReview(
            claim_id=item.claim_id, verdict=SemanticVerdict(item.verdict),
            reviewed_fragment_ids=tuple(item.reviewed_fragment_ids),
        ) for item in payload.semantic_reviews)
        decision = verify_claims(claims, evidence=state.evidence, facts=state.facts,
                                  as_of_date=state.as_of_date, semantic_reviews=reviews)
        actions = [item.text for item in claims if item.kind == ClaimKind.ACTION]
        if not decision.analysis_allowed or not actions:
            raise ApiError(status_code=422, code="LAWYER_WORK_PRODUCT_NOT_VERIFIED",
                           message="No complete evidence-verified action plan was produced")
        summary = state.facts.get(FactKey.EVENT_SUMMARY, state.facts.get(FactKey.PROBLEM_SUMMARY))
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1500:
            raise ApiError(status_code=422, code="LAWYER_WORK_PRODUCT_SUMMARY_UNAVAILABLE",
                           message="A confirmed concise case summary is required")
        fragment_ids = list(dict.fromkeys(identifier for item in decision.claims
                                          for identifier in item.verified_fragment_ids))
        content = InternalWorkProduct(
            schemaVersion="ai-first-work-product.v1", kind="LAWYER_REVIEW_DRAFT",
            workProductId=previous.work_product_id if previous is not None else uuid4(),
            version=previous.version + 1 if previous is not None else 1,
            parentVersion=previous.version if previous is not None else None,
            clinicId=actor.clinic_id,
            source=source, asOfDate=state.as_of_date,
            riskPolicyVersion=state.risk_policy.domain.version,
            status="UNAPPROVED", summary=summary, actions=actions,
            uncertainties=_uncertainties(state.facts),
            legalFragmentIds=fragment_ids,
        )
        row = _new_version(actor, content, origin="AI", review_state="UNAPPROVED")
        escalation = await session.get(CaseEscalation, loaded.response.escalation_id)
        assert escalation is not None
        verifier = await record_verifier_run(
            session, clinic_id=actor.clinic_id, case_id=case_id,
            actor_membership_id=actor.membership_id,
            case_risk_assessment_id=escalation.case_risk_assessment_id,
            as_of_date=state.as_of_date, fact_snapshot_sha256=state.fact_snapshot_sha256,
            evidence_trace_sha256=state.evidence_trace_sha256,
            payload=build_verifier_run_payload(claims, decision),
        )
        session.add(row)
        await session.flush()
        _retain_receipt(session, actor=actor, row=row, scope=scope, key=idempotency_key,
                         request_hash=request_hash)
        session.add(_audit(actor=actor, action="LAWYER_WORK_PRODUCT_CREATED",
                           resource_type="CASE_WORK_PRODUCT", resource_id=row.id,
                           metadata={"caseId": str(case_id), "version": row.version,
                                     "contentSha256": row.content_sha256,
                                     "requestSha256": request_hash,
                                     "operationKey": str(idempotency_key),
                                     "verifierRunId": str(verifier.analysis_run_id),
                                     "sourceOperationId": str(source.operation_id)}))
        view = await _view(session, actor=actor, case=state.case, row=row, loaded_state=state)
        if not view.is_current:
            raise _stale()
        await _private_finish(session, actor=actor, case=state.case,
                               telegram_user_id=telegram_user_id, job_id=x_analysis_job_id,
                               job_token=x_analysis_job_token)
        await _commit_with_reference_fence(
            session, case=state.case, reference_trace=state.reference_example_trace_sha256,
            material_context=state.case_material_context,
        )
        return view

    @router.get("/v1/case-escalations/{escalation_id}/work-product",
                response_model=LawyerWorkProductView)
    async def get(escalation_id: UUID, telegram_user_id: TelegramUserId,
                   session: Session) -> LawyerWorkProductView:
        actor, case, _ = await _public_case(session, telegram_user_id=telegram_user_id,
                                           escalation_id=escalation_id)
        row = await _latest(session, actor, case.id)
        if row is None:
            raise _missing()
        result = await _view(session, actor=actor, case=case, row=row)
        if actor.role == "CLINIC_ADMIN" and (row.review_state != "HUMAN_APPROVED"
                                             or not result.is_current):
            raise _missing()
        await recheck_submission_access(session, actor=actor, telegram_user_id=telegram_user_id,
                                        case=case)
        session.add(_audit(actor=actor, action="LAWYER_WORK_PRODUCT_READ",
                           resource_type="CASE_WORK_PRODUCT", resource_id=row.id,
                           metadata={"caseId": str(case.id), "version": row.version,
                                     "reviewState": row.review_state,
                                     "isCurrent": result.is_current}))
        await session.commit()
        return result

    @router.post("/v1/case-escalations/{escalation_id}/work-product/reviews",
                 response_model=LawyerWorkProductView, status_code=201)
    async def review(escalation_id: UUID, payload: LawyerWorkProductReview,
                      telegram_user_id: TelegramUserId, idempotency_key: IdempotencyKey,
                      response: Response, session: Session) -> LawyerWorkProductView:
        actor, case, _ = await _public_case(session, telegram_user_id=telegram_user_id,
                                           escalation_id=escalation_id, mutation=True)
        scope, request_hash = f"lawyer-product:review:{escalation_id}", _canonical_hash(
            payload.model_dump(mode="json", by_alias=True),
        )
        replay = await _receipt_row(session, actor=actor, case=case, scope=scope,
                                     key=idempotency_key, request_hash=request_hash)
        material_contexts: list[CaseMaterialContextState] = []
        if replay is not None:
            response.status_code = 200
            result = await _view(session, actor=actor, case=case, row=replay,
                                 material_contexts=material_contexts)
            await recheck_submission_access(session, actor=actor, telegram_user_id=telegram_user_id,
                                            case=case)
            _require_enabled()
            require_analysis_running()
            await _commit_with_reference_fence(
                session, case=case,
                reference_trace=(result.content.source.context.reference_example_trace_sha256
                                 if result.is_current and result.content.source.context else None),
                material_context=material_contexts[-1] if material_contexts else None,
            )
            return result
        row = await _latest(session, actor, case.id)
        if (row is None or row.id != payload.expected_version_id
                or row.content_sha256 != payload.expected_content_sha256):
            raise _stale()
        if row.review_state == "HUMAN_APPROVED" and payload.decision == "APPROVE":
            raise _stale()
        existing = await _view(session, actor=actor, case=case, row=row,
                               material_contexts=material_contexts)
        if not existing.is_current:
            raise _stale()
        wire = existing.content.model_dump(mode="json", by_alias=True)
        wire.update(version=row.version + 1, parentVersion=row.version)
        for name in ("summary", "actions", "uncertainties"):
            value = getattr(payload, name)
            if value is not None:
                wire[name] = value
        content = InternalWorkProduct.model_validate(wire)
        new_row = _new_version(actor, content, origin="HUMAN", review_state=(
            "HUMAN_APPROVED" if payload.decision == "APPROVE" else "HUMAN_REVISED"
        ))
        session.add(new_row)
        await session.flush()
        _retain_receipt(session, actor=actor, row=new_row, scope=scope, key=idempotency_key,
                         request_hash=request_hash)
        session.add(_audit(actor=actor, action="LAWYER_WORK_PRODUCT_REVIEWED",
                           resource_type="CASE_WORK_PRODUCT", resource_id=new_row.id,
                           metadata={"caseId": str(case.id), "version": new_row.version,
                                     "parentVersionId": str(row.id), "decision": payload.decision,
                                     "requestSha256": request_hash,
                                     "operationKey": str(idempotency_key),
                                     "contentSha256": new_row.content_sha256}))
        # Source/case/assignment locks from the displayed-version check remain
        # held. Human edits change no source identity; no network retrieval belongs
        # between this flush and the final authorization/commit.
        result = LawyerWorkProductView.model_validate({
            "versionId": new_row.id, "reviewState": new_row.review_state, "origin": "HUMAN",
            "isCurrent": True, "contentSha256": new_row.content_sha256,
            "content": content, "citations": existing.citations,
        })
        await recheck_submission_access(session, actor=actor, telegram_user_id=telegram_user_id,
                                        case=case)
        _require_enabled()
        require_analysis_running()
        await _commit_with_reference_fence(
            session, case=case,
            reference_trace=(content.source.context.reference_example_trace_sha256
                             if content.source.context else None),
            material_context=material_contexts[-1] if material_contexts else None,
        )
        return result

    return router
