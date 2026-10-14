"""Creator-only new-draft recovery; confirmed case history is never rewritten."""

from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis_clarifications import validate_request_scope
from legal_core.api_contracts import (
    TelegramDraftWizardState,
    TelegramIntakeDraftResponse,
    TelegramIntakeDraftUpdateRequest,
)
from legal_core.case_api import (
    DRAFT_ACTIVE_LIMIT,
    DRAFT_RETENTION,
    ActorContext,
    ApiError,
    IdempotencyKey,
    TelegramUserId,
    _audit,
    _canonical_hash,
    _current_fact_rows,
    _domain_facts,
    _draft_response,
    _finish_idempotency,
    _idempotency_replay,
    _new_idempotency_record,
    _require_case_content_available,
    _require_case_intake_actor,
    _require_finalized_case,
    resolve_actor,
)
from legal_core.contracts import CanonicalReport, ContractModel, FactKey
from legal_core.models import Case, CaseReport, ClinicUser, TelegramIntakeDraft
from legal_core.risk_engine import fact_snapshot_sha256

_FIELDS = {
    FactKey.INCOMING_COMMUNICATION: "incomingKind",
    FactKey.INCOMING_SOURCE_STATUS: "incomingSourceStatus",
    FactKey.SITUATION_AREAS: "situationAreas",
    FactKey.AFFECTED_SERVICES: "affectedServices",
    FactKey.EVENT_SUMMARY: "eventSummary",
    FactKey.EVENT_DATE: "eventDate",
    FactKey.CONFLICT_STAGE: "conflictStage",
    FactKey.CLINIC_ACTIONS: "clinicActions",
    FactKey.HEALTH_CONSEQUENCE_SIGNALS: "healthSignals",
    FactKey.CASE_MATERIALS_STATUS: "caseMaterialsStatus",
    FactKey.FACTUAL_SAFETY_SCREENING: "safetyScreening",
    FactKey.ANALYSIS_CLARIFICATION_ANSWERS: "analysisClarificationAnswers",
}


class ClarificationDraftRequest(ContractModel):
    expected_fact_snapshot_sha256: str = Field(
        alias="expectedFactSnapshotSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    expected_report_id: UUID | None = Field(
        default=None, alias="expectedReportId", exclude_if=lambda value: value is None
    )
    expected_report_sha256: str | None = Field(
        default=None, alias="expectedReportSha256", pattern=r"^[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_report_binding(self) -> "ClarificationDraftRequest":
        if (self.expected_report_id is None) != (self.expected_report_sha256 is None):
            raise ValueError("expectedReportId and expectedReportSha256 must be provided together")
        return self


class ClarificationDraftPreview(ClarificationDraftRequest):
    case_id: UUID = Field(alias="caseId")


async def _source(
    session: AsyncSession,
    actor: ActorContext,
    case_id: UUID,
    report_id: UUID | None = None,
) -> tuple[dict[FactKey, object], str, CaseReport, CanonicalReport]:
    case = await session.scalar(select(Case).where(
        Case.id == case_id, Case.clinic_id == actor.clinic_id,
        Case.created_by_membership_id == actor.membership_id,
    ).with_for_update())
    if case is None:
        raise ApiError(status_code=404, code="CASE_NOT_FOUND", message="Case not found")
    _require_case_content_available(case)
    _require_finalized_case(case)
    if case.intake_schema_version != "dental-case-intake.v2":
        raise ApiError(
            status_code=409,
            code="CLARIFICATION_SCHEMA_UNSUPPORTED",
            message="Create a new guided case for this intake version",
        )
    facts = _domain_facts(await _current_fact_rows(session, case.id))
    source_hash = fact_snapshot_sha256(facts)
    report_query = select(CaseReport).where(
        CaseReport.case_id == case.id, CaseReport.clinic_id == actor.clinic_id,
    )
    if report_id is not None:
        report_query = report_query.where(CaseReport.id == report_id)
    report = await session.scalar(
        report_query.order_by(CaseReport.report_version.desc()).limit(1).with_for_update()
    )
    if report is None:
        raise ApiError(status_code=409, code="CLARIFICATION_REPORT_UNAVAILABLE",
                       message="The retained source report is unavailable")
    try:
        canonical = CanonicalReport.model_validate(report.report_json)
        if (
            canonical.report_id != report.id or canonical.case.id != case.id
            or canonical.report_version != report.report_version
            or report.facts_snapshot_sha256 != source_hash
            or canonical.fact_snapshot_sha256 != source_hash
            or canonical.facts != {key.value: value for key, value in facts.items()}
            or _canonical_hash(report.report_json) != report.content_sha256
        ):
            raise ValueError("source report does not bind the current confirmed case facts")
        validate_request_scope(canonical.clarification_requests, canonical.facts)
    except (ValidationError, ValueError) as exc:
        raise ApiError(status_code=409, code="CLARIFICATION_REPORT_INVALID",
                       message="The retained source report cannot be used for recovery") from exc
    return facts, source_hash, report, canonical


def _prefill(
    facts: dict[FactKey, object],
    case_id: UUID,
    source_hash: str,
    source_report: CanonicalReport | None = None,
    source_report_sha256: str | None = None,
) -> tuple[TelegramDraftWizardState, dict[str, Any]]:
    data: dict[str, Any] = {"intakeVersion": 2}
    for key, field in _FIELDS.items():
        if key in facts:
            data[field] = deepcopy(facts[key])
    data.update(incomingSourceStatus="NOT_ATTACHED", caseMaterialsStatus="NOT_ATTACHED")
    screening = data.get("safetyScreening")
    if isinstance(screening, dict):
        data["safetyScreening"] = {k: v for k, v in screening.items() if v != "UNKNOWN"}
    data["correctionSource"] = {"caseId": str(case_id), "factSnapshotSha256": source_hash}
    if source_report is not None:
        data["correctionSource"].update(
            reportId=str(source_report.report_id),
            reportSha256=source_report_sha256 or _canonical_hash(
                source_report.model_dump(mode="json", by_alias=True)
            ),
        )
        if source_report.clarification_requests:
            data["analysisClarificationRequests"] = [
                request.model_dump(mode="json", by_alias=True, exclude_none=True)
                for request in source_report.clarification_requests
            ]
    event_date = data.get("eventDate")
    state = (
        "SAFETY"
        if isinstance(event_date, dict) and event_date.get("precision") == "EXACT"
        else "EVENT_DATE"
    )
    if data.get("analysisClarificationRequests"):
        state = "ANALYSIS_CLARIFICATION"
    # Apply the same bounded shape/version contract as subsequent gateway saves.
    validated = TelegramIntakeDraftUpdateRequest.model_validate(
        {
            "expectedRevision": 1,
            "wizardState": state,
            "draftData": data,
        }
    )
    return validated.wizard_state, validated.draft_data


def create_clarification_router(sessions: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter(prefix="/cases", tags=["case-clarification"])

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    Session = Annotated[AsyncSession, Depends(session_dependency)]

    @router.get("/{case_id}/clarification-drafts/preview", response_model=ClarificationDraftPreview)
    async def preview(
        case_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
        report_id: UUID | None = None,
    ) -> ClarificationDraftPreview:
        actor = await resolve_actor(session, telegram_user_id)
        _require_case_intake_actor(actor)
        _, source_hash, report, _ = await _source(session, actor, case_id, report_id=report_id)
        return ClarificationDraftPreview(
            caseId=case_id, expectedFactSnapshotSha256=source_hash,
            expectedReportId=report.id, expectedReportSha256=report.content_sha256,
        )

    @router.post(
        "/{case_id}/clarification-drafts",
        response_model=TelegramIntakeDraftResponse,
        status_code=201,
    )
    async def create_draft(
        case_id: UUID,
        payload: ClarificationDraftRequest,
        response: Response,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> TelegramIntakeDraftResponse:
        actor = await resolve_actor(session, telegram_user_id)
        _require_case_intake_actor(actor)
        facts, source_hash, report, canonical = await _source(session, actor, case_id)
        scope = f"case-clarification:{case_id}"
        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        replay = await _idempotency_replay(
            session, actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        if replay is not None:
            response.status_code = 200
            return TelegramIntakeDraftResponse.model_validate(replay)
        if payload.expected_fact_snapshot_sha256 != source_hash:
            raise ApiError(
                status_code=409,
                code="CASE_FACTS_STALE",
                message="Confirmed facts do not match the expected snapshot",
            )
        if payload.expected_report_id is None and canonical.clarification_requests:
            raise ApiError(
                status_code=409, code="CLARIFICATION_REPORT_BINDING_REQUIRED",
                message="Recovery requires the displayed source report binding",
            )
        if payload.expected_report_id is not None and (
            payload.expected_report_id != report.id
            or payload.expected_report_sha256 != report.content_sha256
        ):
            raise ApiError(status_code=409, code="CASE_REPORT_STALE",
                           message="The source report does not match the expected report")
        await session.scalar(
            select(ClinicUser)
            .where(
                ClinicUser.id == actor.membership_id,
            )
            .with_for_update()
        )
        count = await session.scalar(
            select(func.count())
            .select_from(TelegramIntakeDraft)
            .where(
                TelegramIntakeDraft.clinic_id == actor.clinic_id,
                TelegramIntakeDraft.actor_membership_id == actor.membership_id,
                TelegramIntakeDraft.status == "DRAFT",
            )
        )
        if count is not None and count >= DRAFT_ACTIVE_LIMIT:
            raise ApiError(
                status_code=409,
                code="INTAKE_DRAFT_LIMIT_REACHED",
                message="The active intake draft limit was reached",
            )
        state, data = _prefill(
            facts, case_id, source_hash,
            canonical if payload.expected_report_id is not None else None,
            report.content_sha256 if payload.expected_report_id is not None else None,
        )
        now = datetime.now(UTC)
        draft = TelegramIntakeDraft(
            clinic_id=actor.clinic_id,
            actor_membership_id=actor.membership_id,
            status="DRAFT",
            wizard_state=state,
            draft_json=data,
            revision=1,
            created_at=now,
            updated_at=now,
            purge_after=now + DRAFT_RETENTION,
        )
        record = _new_idempotency_record(
            actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        session.add_all([draft, record])
        await session.flush()
        result = _draft_response(draft)
        _finish_idempotency(
            record,
            resource_type="TELEGRAM_INTAKE_DRAFT",
            resource_id=draft.id,
            response_json=result.model_dump(mode="json", by_alias=True),
        )
        session.add(
            _audit(
                actor=actor,
                action="CASE_CLARIFICATION_DRAFT_CREATED",
                resource_type="TELEGRAM_INTAKE_DRAFT",
                resource_id=draft.id,
                metadata={
                    "sourceCaseId": str(case_id), "sourceFactSha256": source_hash,
                    "sourceReportId": str(report.id), "sourceReportSha256": report.content_sha256,
                    "clarificationRequestCount": len(canonical.clarification_requests),
                },
            )
        )
        await session.commit()
        return result

    return router
