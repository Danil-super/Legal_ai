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
from legal_core.analysis_contracts import PreparationResult
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
from legal_core.case_preparation import read_preparation_replay, recheck_submission_access
from legal_core.contracts import CanonicalReport, ContractModel, FactKey
from legal_core.factual_safety_intake import FactualSafetyScreening
from legal_core.models import (
    AuditEvent,
    Case,
    CaseReport,
    ClinicUser,
    IdempotencyRecord,
    TelegramIntakeDraft,
)
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
    expected_operation_id: UUID | None = Field(
        default=None, alias="expectedOperationId", exclude_if=lambda value: value is None,
    )
    expected_operation_sha256: str | None = Field(
        default=None, alias="expectedOperationSha256", pattern=r"^[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_report_binding(self) -> "ClarificationDraftRequest":
        if (self.expected_report_id is None) != (self.expected_report_sha256 is None):
            raise ValueError("expectedReportId and expectedReportSha256 must be provided together")
        if (self.expected_operation_id is None) != (self.expected_operation_sha256 is None):
            raise ValueError("operation ID and hash must be provided together")
        if self.expected_operation_id is not None and self.expected_report_id is not None:
            raise ValueError("operation and report origins are mutually exclusive")
        return self


class ClarificationDraftPreview(ClarificationDraftRequest):
    case_id: UUID = Field(alias="caseId")


async def _source(
    session: AsyncSession,
    actor: ActorContext,
    case_id: UUID,
    report_id: UUID | None = None,
    operation_id: UUID | None = None,
    telegram_user_id: int | None = None,
) -> tuple[dict[FactKey, object], str, CaseReport | PreparationResult, CanonicalReport | None]:
    case = await session.scalar(select(Case).where(
        Case.id == case_id, Case.clinic_id == actor.clinic_id,
        Case.created_by_membership_id == actor.membership_id,
    ).with_for_update().execution_options(populate_existing=True))
    if case is None:
        raise ApiError(status_code=404, code="CASE_NOT_FOUND", message="Case not found")
    _require_case_content_available(case)
    if operation_id is not None:
        assert telegram_user_id is not None
        await recheck_submission_access(session, actor=actor, telegram_user_id=telegram_user_id,
                                        case=case)
    _require_finalized_case(case)
    if case.intake_schema_version != "dental-case-intake.v2":
        raise ApiError(
            status_code=409,
            code="CLARIFICATION_SCHEMA_UNSUPPORTED",
            message="Create a new guided case for this intake version",
        )
    facts = _domain_facts(await _current_fact_rows(session, case.id))
    source_hash = fact_snapshot_sha256(facts)
    if operation_id is not None:
        # Select latest first, including READY/HUMAN_REVIEW. Never revive an old stop.
        record = await session.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.clinic_id == actor.clinic_id,
            IdempotencyRecord.actor_membership_id == actor.membership_id,
            IdempotencyRecord.scope == f"cases:{case_id}:preparation-submissions",
            IdempotencyRecord.state == "SUCCEEDED",
        ).order_by(IdempotencyRecord.created_at.desc(), IdempotencyRecord.id.desc()).limit(1))
        if record is None or record.response_json is None:
            raise ApiError(status_code=409, code="CLARIFICATION_OPERATION_UNAVAILABLE",
                           message="The retained preparation is unavailable")
        try:
            receipt_key = UUID(record.key)
        except ValueError as exc:
            raise ApiError(status_code=502, code="PREPARATION_RESULT_INVALID",
                           message="Stored preparation is unavailable") from exc
        prepared = await read_preparation_replay(
            session, clinic_id=actor.clinic_id, actor_membership_id=actor.membership_id,
            case=case, scope=record.scope, idempotency_key=receipt_key,
            wire=record.response_json,
        )
        if prepared.root.source.operation_id != operation_id:
            raise ApiError(status_code=409, code="CASE_PREPARATION_STALE",
                           message="The preparation source has changed")
        if prepared.root.status not in {"NEEDS_FACTS", "LIMITED"}:
            raise ApiError(status_code=409, code="CLARIFICATION_OPERATION_UNAVAILABLE",
                           message="This result does not require a clarification draft")
        if prepared.root.source.fact_snapshot_sha256 != source_hash:
            raise ApiError(status_code=409, code="CASE_FACTS_STALE",
                           message="The preparation does not match confirmed facts")
        if prepared.root.status == "NEEDS_FACTS":
            try:
                validate_request_scope(prepared.root.clarification_requests,
                                       {key.value: value for key, value in facts.items()})
            except ValueError as exc:
                raise ApiError(status_code=409, code="CLARIFICATION_OPERATION_INVALID",
                               message="Preparation questions cannot be used for recovery") from exc
        return facts, source_hash, prepared, None
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
    source_preparation: PreparationResult | None = None,
) -> tuple[TelegramDraftWizardState, dict[str, Any]]:
    data: dict[str, Any] = {"intakeVersion": 2}
    for key, field in _FIELDS.items():
        if key in facts:
            data[field] = deepcopy(facts[key])
    data.update(incomingSourceStatus="NOT_ATTACHED", caseMaterialsStatus="NOT_ATTACHED")
    screening = data.get("safetyScreening")
    if source_preparation is None and isinstance(screening, dict):
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
    if source_preparation is not None:
        data["correctionSource"].update(
            operationId=str(source_preparation.root.source.operation_id),
            operationSha256=_canonical_hash(
                source_preparation.model_dump(mode="json", by_alias=True)
            ),
        )
        if source_preparation.root.status == "NEEDS_FACTS":
            data["analysisClarificationRequests"] = [
                request.model_dump(mode="json", by_alias=True)
                for request in source_preparation.root.clarification_requests
            ]
    event_date = data.get("eventDate")
    state = (
        "SAFETY"
        if isinstance(event_date, dict) and event_date.get("precision") == "EXACT"
        else "EVENT_DATE"
    )
    if source_preparation is not None and isinstance(event_date, dict):
        # Attempted UNKNOWN/APPROXIMATE is not an unanswered question.
        try:
            FactualSafetyScreening.model_validate(screening)
            state = "V2_CONFIRM"
        except ValidationError:
            state = "SAFETY"
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
        operation_id: UUID | None = None,
    ) -> ClarificationDraftPreview:
        actor = await resolve_actor(session, telegram_user_id)
        _require_case_intake_actor(actor)
        if report_id is not None and operation_id is not None:
            raise ApiError(status_code=422, code="CLARIFICATION_ORIGIN_INVALID",
                           message="Choose one source origin")
        _, source_hash, report, _ = await _source(
            session, actor, case_id, report_id=report_id, operation_id=operation_id,
            telegram_user_id=telegram_user_id,
        )
        if isinstance(report, PreparationResult):
            return ClarificationDraftPreview(
                caseId=case_id, expectedFactSnapshotSha256=source_hash,
                expectedOperationId=report.root.source.operation_id,
                expectedOperationSha256=_canonical_hash(
                    report.model_dump(mode="json", by_alias=True)
                ),
            )
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
        facts, source_hash, origin, canonical = await _source(
            session, actor, case_id, operation_id=payload.expected_operation_id,
            telegram_user_id=telegram_user_id,
        )
        preparation = origin if isinstance(origin, PreparationResult) else None
        report = origin if isinstance(origin, CaseReport) else None
        if preparation is not None and (
            payload.expected_fact_snapshot_sha256 != source_hash
            or payload.expected_operation_sha256 != _canonical_hash(
                preparation.model_dump(mode="json", by_alias=True)
            )
        ):
            raise ApiError(status_code=409, code="CASE_PREPARATION_STALE",
                           message="The source preparation does not match the expected result")
        scope = f"case-clarification:{case_id}"
        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        replay = await _idempotency_replay(
            session, actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        if replay is not None:
            if preparation is not None:
                try:
                    cached = TelegramIntakeDraftResponse.model_validate(replay)
                except ValueError as exc:
                    raise ApiError(status_code=502, code="CLARIFICATION_RESULT_INVALID",
                                   message="The saved clarification is unavailable") from exc
                draft = await session.scalar(select(TelegramIntakeDraft).join(
                    IdempotencyRecord,
                    IdempotencyRecord.resource_id == TelegramIntakeDraft.id,
                ).where(
                    IdempotencyRecord.clinic_id == actor.clinic_id,
                    IdempotencyRecord.actor_membership_id == actor.membership_id,
                    IdempotencyRecord.scope == scope,
                    IdempotencyRecord.key == str(idempotency_key),
                    IdempotencyRecord.resource_type == "TELEGRAM_INTAKE_DRAFT",
                    TelegramIntakeDraft.clinic_id == actor.clinic_id,
                    TelegramIntakeDraft.actor_membership_id == actor.membership_id,
                ))
                expected_source = {
                    "caseId": str(case_id), "factSnapshotSha256": source_hash,
                    "operationId": str(payload.expected_operation_id),
                    "operationSha256": payload.expected_operation_sha256,
                }
                audit = None if draft is None else await session.scalar(select(AuditEvent.id).where(
                    AuditEvent.clinic_id == actor.clinic_id,
                    AuditEvent.actor_membership_id == actor.membership_id,
                    AuditEvent.resource_type == "TELEGRAM_INTAKE_DRAFT",
                    AuditEvent.resource_id == draft.id,
                    AuditEvent.action == "CASE_CLARIFICATION_DRAFT_CREATED",
                    AuditEvent.metadata_json["sourceCaseId"].as_string() == str(case_id),
                    AuditEvent.metadata_json["sourceFactSha256"].as_string() == source_hash,
                    AuditEvent.metadata_json["sourceOperationId"].as_string()
                    == str(payload.expected_operation_id),
                    AuditEvent.metadata_json["sourceOperationSha256"].as_string()
                    == payload.expected_operation_sha256,
                    AuditEvent.metadata_json["requestBindingSha256"].as_string()
                    == _canonical_hash({
                        "scope": scope, "key": str(idempotency_key), "requestSha256": request_hash,
                    }),
                ))
                if (draft is None or cached.id != draft.id
                        or audit is None
                        or draft.draft_json.get("correctionSource") != expected_source
                        or cached.draft_data.get("correctionSource") != expected_source):
                    raise ApiError(status_code=502, code="CLARIFICATION_RESULT_INVALID",
                                   message="The saved clarification is unavailable")
                if draft.status != "DRAFT" or draft.purge_after <= datetime.now(UTC):
                    raise ApiError(status_code=410, code="INTAKE_DRAFT_EXPIRED",
                                   message="The clarification draft is no longer available")
                # The owned durable draft, not a mutable cached snapshot, is authoritative.
                response.status_code = 200
                return _draft_response(draft)
            response.status_code = 200
            return TelegramIntakeDraftResponse.model_validate(replay)
        if payload.expected_fact_snapshot_sha256 != source_hash:
            raise ApiError(
                status_code=409,
                code="CASE_FACTS_STALE",
                message="Confirmed facts do not match the expected snapshot",
            )
        if payload.expected_report_id is None and canonical and canonical.clarification_requests:
            raise ApiError(
                status_code=409, code="CLARIFICATION_REPORT_BINDING_REQUIRED",
                message="Recovery requires the displayed source report binding",
            )
        if payload.expected_report_id is not None and report is not None and (
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
        if preparation is not None:
            # The member lock can wait too: reauthorize and revalidate before writing.
            await _source(session, actor, case_id, operation_id=payload.expected_operation_id,
                          telegram_user_id=telegram_user_id)
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
            report.content_sha256 if report is not None and payload.expected_report_id else None,
            source_preparation=preparation,
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
                    **({"sourceReportId": str(report.id),
                        "sourceReportSha256": report.content_sha256} if report is not None else {
                        "sourceOperationId": str(payload.expected_operation_id),
                        "sourceOperationSha256": payload.expected_operation_sha256,
                        "requestBindingSha256": _canonical_hash({
                            "scope": scope, "key": str(idempotency_key),
                            "requestSha256": request_hash,
                        }),
                    }),
                    "clarificationRequestCount": len(data.get("analysisClarificationRequests", [])),
                },
            )
        )
        await session.commit()
        return result

    return router
