"""Tenant-scoped Case Core API backed by PostgreSQL."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.api_contracts import (
    ActorResponse,
    AddFactsRequest,
    CaseResponse,
    CreateCaseRequest,
    CreateReportRequest,
    FinalizeRequest,
    IntakeResponse,
    ReportResponse,
    TelegramWorkflowResponse,
    TelegramWorkflowSubmissionRequest,
)
from legal_core.contracts import CanonicalReport, CaseStatus, FactKey
from legal_core.intake import missing_facts_for
from legal_core.models import (
    AuditEvent,
    Case,
    CaseFact,
    CaseReport,
    ClinicUser,
    IdempotencyRecord,
    TelegramCaseWorkflow,
    User,
)
from legal_core.reports import build_intake_report, render_report_pdf

TelegramUserId = Annotated[int, Header(alias="X-Telegram-User-Id", gt=0)]
IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]


class ApiError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class ActorContext:
    user_id: UUID
    membership_id: UUID
    clinic_id: UUID
    role: str


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _case_response(case: Case) -> CaseResponse:
    return CaseResponse(
        id=case.id,
        publicNumber=f"DL-{case.created_at.year}-{case.case_no:06d}",
        status=CaseStatus(case.status),
        intakeSchemaVersion=case.intake_schema_version,
        createdAt=case.created_at,
    )


async def resolve_actor(session: AsyncSession, telegram_user_id: int) -> ActorContext:
    result = await session.execute(
        select(User.id, ClinicUser.id, ClinicUser.clinic_id, ClinicUser.role)
        .join(ClinicUser, ClinicUser.user_id == User.id)
        .where(
            User.telegram_user_id == telegram_user_id,
            User.status == "ACTIVE",
            ClinicUser.status == "ACTIVE",
            ClinicUser.role == "CLINIC_ADMIN",
        )
    )
    memberships = result.all()
    if len(memberships) != 1:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="ACTOR_NOT_AUTHORIZED",
            message="Telegram user is not mapped to one active clinic administrator",
        )
    user_id, membership_id, clinic_id, role = memberships[0]
    await session.execute(select(func.set_config("app.current_clinic_id", str(clinic_id), True)))
    return ActorContext(
        user_id=user_id,
        membership_id=membership_id,
        clinic_id=clinic_id,
        role=role,
    )


async def _idempotency_replay(
    session: AsyncSession,
    *,
    actor: ActorContext,
    scope: str,
    key: UUID,
    request_hash: str,
) -> dict[str, Any] | None:
    record = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.actor_membership_id == actor.membership_id,
            IdempotencyRecord.key == str(key),
        )
    )
    if record is None:
        return None
    if record.request_sha256 != request_hash:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="IDEMPOTENCY_KEY_REUSED",
            message="Idempotency key was already used with another request",
        )
    if record.state != "SUCCEEDED" or record.response_json is None:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="REQUEST_IN_PROGRESS",
            message="The original request is still in progress",
        )
    return record.response_json


def _new_idempotency_record(
    *,
    actor: ActorContext,
    scope: str,
    key: UUID,
    request_hash: str,
) -> IdempotencyRecord:
    return IdempotencyRecord(
        clinic_id=actor.clinic_id,
        actor_membership_id=actor.membership_id,
        scope=scope,
        key=str(key),
        request_sha256=request_hash,
        state="IN_PROGRESS",
    )


def _finish_idempotency(
    record: IdempotencyRecord,
    *,
    resource_type: str,
    resource_id: UUID,
    response_json: dict[str, Any],
) -> None:
    record.state = "SUCCEEDED"
    record.resource_type = resource_type
    record.resource_id = resource_id
    record.response_json = response_json


def _audit(
    *,
    actor: ActorContext,
    action: str,
    resource_type: str,
    resource_id: UUID,
    metadata: dict[str, Any],
) -> AuditEvent:
    return AuditEvent(
        clinic_id=actor.clinic_id,
        actor_membership_id=actor.membership_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=metadata,
        correlation_id=uuid4(),
    )


async def _tenant_case(session: AsyncSession, actor: ActorContext, case_id: UUID) -> Case:
    case = await session.scalar(
        select(Case).where(Case.id == case_id, Case.clinic_id == actor.clinic_id)
    )
    if case is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="CASE_NOT_FOUND",
            message="Case not found",
        )
    return case


async def _current_fact_rows(session: AsyncSession, case_id: UUID) -> list[CaseFact]:
    result = await session.scalars(
        select(CaseFact)
        .where(CaseFact.case_id == case_id)
        .order_by(CaseFact.fact_key, CaseFact.revision.desc())
    )
    current: dict[str, CaseFact] = {}
    for fact in result:
        current.setdefault(fact.fact_key, fact)
    return list(current.values())


def _domain_value(fact: CaseFact) -> object:
    value = fact.value_json
    if fact.value_type == "TEXT":
        return value.get("text")
    if fact.value_type == "BOOLEAN":
        return value.get("boolean")
    if fact.value_type == "ENUM":
        return value.get("value")
    if fact.value_type == "ENUM_SET":
        return value.get("values")
    return value


def _domain_facts(rows: list[CaseFact]) -> dict[FactKey, object]:
    return {FactKey(row.fact_key): _domain_value(row) for row in rows}


def _input_value(value_type: str, value: dict[str, Any]) -> object:
    if value_type == "TEXT":
        return value.get("text")
    if value_type == "BOOLEAN":
        return value.get("boolean")
    if value_type == "ENUM":
        return value.get("value")
    if value_type == "ENUM_SET":
        return value.get("values")
    return value


async def _workflow_response(
    session: AsyncSession, workflow: TelegramCaseWorkflow
) -> TelegramWorkflowResponse:
    case = await session.scalar(
        select(Case).where(
            Case.id == workflow.case_id,
            Case.clinic_id == workflow.clinic_id,
        )
    )
    report = await session.scalar(
        select(CaseReport).where(
            CaseReport.id == workflow.report_id,
            CaseReport.clinic_id == workflow.clinic_id,
            CaseReport.case_id == workflow.case_id,
        )
    )
    if case is None or report is None:
        raise RuntimeError("durable workflow references are inconsistent")
    return TelegramWorkflowResponse(
        workflowId=workflow.id,
        state="SUCCEEDED",
        case=_case_response(case),
        report=ReportResponse(
            id=report.id,
            caseId=report.case_id,
            reportVersion=report.report_version,
            reportJson=report.report_json,
            pdfSha256=report.pdf_sha256,
            createdAt=report.created_at,
        ),
    )


def _intake_response(case: Case, facts: dict[FactKey, object]) -> IntakeResponse:
    missing = missing_facts_for(facts)
    return IntakeResponse(
        caseId=case.id,
        status=CaseStatus(case.status),
        missingFacts=missing,
        nextQuestionId=missing[0].question_id if missing else None,
    )


def create_case_router(
    session_factory: async_sessionmaker[AsyncSession],
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["cases"])

    async def get_session() -> Any:
        async with session_factory() as session:
            yield session

    Session = Annotated[AsyncSession, Depends(get_session)]

    @router.get("/actor", response_model=ActorResponse)
    async def get_actor(
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> ActorResponse:
        await resolve_actor(session, telegram_user_id)
        return ActorResponse(role="CLINIC_ADMIN")

    @router.post("/cases", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
    async def create_case(
        payload: CreateCaseRequest,
        response: Response,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> CaseResponse:
        actor = await resolve_actor(session, telegram_user_id)
        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        replay = await _idempotency_replay(
            session,
            actor=actor,
            scope="cases:create",
            key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = status.HTTP_200_OK
            return CaseResponse.model_validate(replay)

        idempotency = _new_idempotency_record(
            actor=actor,
            scope="cases:create",
            key=idempotency_key,
            request_hash=request_hash,
        )
        case = Case(
            clinic_id=actor.clinic_id,
            created_by_membership_id=actor.membership_id,
            status=CaseStatus.COLLECTING.value,
            intake_schema_version=payload.intake_schema_version,
        )
        session.add_all([idempotency, case])
        await session.flush()
        result = _case_response(case)
        response_json = result.model_dump(mode="json", by_alias=True)
        _finish_idempotency(
            idempotency,
            resource_type="CASE",
            resource_id=case.id,
            response_json=response_json,
        )
        session.add(
            _audit(
                actor=actor,
                action="CASE_CREATED",
                resource_type="CASE",
                resource_id=case.id,
                metadata={"channel": payload.channel, "schema": payload.intake_schema_version},
            )
        )
        await session.commit()
        return result

    @router.get("/cases/{case_id}", response_model=CaseResponse)
    async def get_case(
        case_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> CaseResponse:
        actor = await resolve_actor(session, telegram_user_id)
        case = await _tenant_case(session, actor, case_id)
        return _case_response(case)

    @router.get("/cases/{case_id}/intake", response_model=IntakeResponse)
    async def get_intake(
        case_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> IntakeResponse:
        actor = await resolve_actor(session, telegram_user_id)
        case = await _tenant_case(session, actor, case_id)
        return _intake_response(case, _domain_facts(await _current_fact_rows(session, case.id)))

    @router.post("/cases/{case_id}/facts", response_model=IntakeResponse)
    async def add_facts(
        case_id: UUID,
        payload: AddFactsRequest,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> IntakeResponse:
        actor = await resolve_actor(session, telegram_user_id)
        case = await _tenant_case(session, actor, case_id)
        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        scope = f"cases:{case_id}:facts"
        replay = await _idempotency_replay(
            session,
            actor=actor,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return IntakeResponse.model_validate(replay)

        idempotency = _new_idempotency_record(
            actor=actor,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        session.add(idempotency)
        current_rows = {row.fact_key: row for row in await _current_fact_rows(session, case.id)}
        for item in payload.facts:
            previous = current_rows.get(item.fact_key.value)
            fact = CaseFact(
                clinic_id=actor.clinic_id,
                case_id=case.id,
                fact_key=item.fact_key.value,
                revision=1 if previous is None else previous.revision + 1,
                value_type=item.value_type,
                value_json=item.value,
                source_type=item.source_type,
                source_ref_json={"questionId": payload.question_id},
                evidence_status="UNVERIFIED",
                recorded_by_membership_id=actor.membership_id,
                supersedes_fact_id=None if previous is None else previous.id,
            )
            session.add(fact)
            current_rows[item.fact_key.value] = fact
        await session.flush()
        facts = _domain_facts(list(current_rows.values()))
        case.status = (
            CaseStatus.NEEDS_INFORMATION.value
            if missing_facts_for(facts)
            else CaseStatus.COLLECTING.value
        )
        case.updated_at = datetime.now(UTC)
        result = _intake_response(case, facts)
        response_json = result.model_dump(mode="json", by_alias=True)
        _finish_idempotency(
            idempotency,
            resource_type="CASE_FACT_BATCH",
            resource_id=case.id,
            response_json=response_json,
        )
        session.add(
            _audit(
                actor=actor,
                action="CASE_FACTS_RECORDED",
                resource_type="CASE",
                resource_id=case.id,
                metadata={
                    "questionId": payload.question_id,
                    "factKeys": [item.fact_key.value for item in payload.facts],
                },
            )
        )
        await session.commit()
        return result

    @router.post("/cases/{case_id}/intake-finalizations", response_model=CaseResponse)
    async def finalize_intake(
        case_id: UUID,
        payload: FinalizeRequest,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> CaseResponse:
        actor = await resolve_actor(session, telegram_user_id)
        case = await _tenant_case(session, actor, case_id)
        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        scope = f"cases:{case_id}:finalize"
        replay = await _idempotency_replay(
            session,
            actor=actor,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return CaseResponse.model_validate(replay)
        facts = _domain_facts(await _current_fact_rows(session, case.id))
        missing = missing_facts_for(facts)
        if missing:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="INSUFFICIENT_FACTS",
                message="Карточку пока нельзя подтвердить",
                details={"missingFactKeys": [item.fact_key.value for item in missing]},
            )

        idempotency = _new_idempotency_record(
            actor=actor,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        case.status = CaseStatus.ANALYSIS_BLOCKED.value
        case.updated_at = datetime.now(UTC)
        result = _case_response(case)
        response_json = result.model_dump(mode="json", by_alias=True)
        _finish_idempotency(
            idempotency,
            resource_type="CASE",
            resource_id=case.id,
            response_json=response_json,
        )
        session.add_all(
            [
                idempotency,
                _audit(
                    actor=actor,
                    action="CASE_INTAKE_FINALIZED",
                    resource_type="CASE",
                    resource_id=case.id,
                    metadata={"analysisStatus": "LEGAL_CORPUS_NOT_READY"},
                ),
            ]
        )
        await session.commit()
        return result

    @router.post(
        "/cases/{case_id}/reports",
        response_model=ReportResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_report(
        case_id: UUID,
        payload: CreateReportRequest,
        response: Response,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> ReportResponse:
        actor = await resolve_actor(session, telegram_user_id)
        case = await _tenant_case(session, actor, case_id)
        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        scope = f"cases:{case_id}:reports"
        replay = await _idempotency_replay(
            session,
            actor=actor,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = status.HTTP_200_OK
            return ReportResponse.model_validate(replay)

        fact_rows = await _current_fact_rows(session, case.id)
        facts = _domain_facts(fact_rows)
        missing = missing_facts_for(facts)
        if missing:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="INSUFFICIENT_FACTS",
                message="Отчёт нельзя сформировать до заполнения карточки",
                details={"missingFactKeys": [item.fact_key.value for item in missing]},
            )
        current_version = await session.scalar(
            select(func.coalesce(func.max(CaseReport.report_version), 0)).where(
                CaseReport.case_id == case.id
            )
        )
        version = int(current_version or 0) + 1
        report_id = uuid4()
        canonical: CanonicalReport = build_intake_report(
            report_id=report_id,
            case_id=case.id,
            public_number=_case_response(case).public_number,
            case_status=CaseStatus(case.status),
            report_version=version,
            generated_at=datetime.now(UTC),
            facts=facts,
            missing_facts=missing,
        )
        report_json = canonical.model_dump(mode="json", by_alias=True)
        pdf = render_report_pdf(canonical)
        pdf_sha256 = hashlib.sha256(pdf).hexdigest()
        row = CaseReport(
            id=report_id,
            clinic_id=actor.clinic_id,
            case_id=case.id,
            report_version=version,
            schema_version=canonical.schema_version,
            status="ANALYSIS_BLOCKED",
            report_json=report_json,
            content_sha256=_canonical_hash(report_json),
            pdf_bytes=pdf,
            pdf_sha256=pdf_sha256,
            pdf_size_bytes=len(pdf),
            facts_snapshot_sha256=canonical.fact_snapshot_sha256,
            created_by_membership_id=actor.membership_id,
        )
        idempotency = _new_idempotency_record(
            actor=actor,
            scope=scope,
            key=idempotency_key,
            request_hash=request_hash,
        )
        session.add_all([row, idempotency])
        await session.flush()
        result = ReportResponse(
            id=row.id,
            caseId=row.case_id,
            reportVersion=row.report_version,
            reportJson=row.report_json,
            pdfSha256=row.pdf_sha256,
            createdAt=row.created_at,
        )
        response_json = result.model_dump(mode="json", by_alias=True)
        _finish_idempotency(
            idempotency,
            resource_type="CASE_REPORT",
            resource_id=row.id,
            response_json=response_json,
        )
        session.add(
            _audit(
                actor=actor,
                action="CASE_REPORT_CREATED",
                resource_type="CASE_REPORT",
                resource_id=row.id,
                metadata={"reportVersion": version, "schemaVersion": row.schema_version},
            )
        )
        await session.commit()
        return result

    @router.post(
        "/telegram-case-workflows/{workflow_id}/submissions",
        response_model=TelegramWorkflowResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def submit_telegram_workflow(
        workflow_id: UUID,
        payload: TelegramWorkflowSubmissionRequest,
        response: Response,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> TelegramWorkflowResponse:
        actor = await resolve_actor(session, telegram_user_id)
        request_json = payload.model_dump(mode="json", by_alias=True)
        request_hash = _canonical_hash(request_json)

        # A transaction-scoped PostgreSQL advisory lock serializes simultaneous retries
        # before the workflow row exists. Sequential retries then replay the stored result.
        lock_key = workflow_id.int & ((1 << 64) - 1)
        if lock_key >= 1 << 63:
            lock_key -= 1 << 64
        await session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        existing = await session.scalar(
            select(TelegramCaseWorkflow).where(
                TelegramCaseWorkflow.id == workflow_id,
                TelegramCaseWorkflow.clinic_id == actor.clinic_id,
                TelegramCaseWorkflow.actor_membership_id == actor.membership_id,
            )
        )
        if existing is not None:
            if existing.request_sha256 != request_hash:
                raise ApiError(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    code="WORKFLOW_PAYLOAD_MISMATCH",
                    message="Workflow identifier was already submitted with other facts",
                )
            response.status_code = status.HTTP_200_OK
            return await _workflow_response(session, existing)

        facts = {
            item.fact_key: _input_value(item.value_type, item.value) for item in payload.facts
        }
        missing = missing_facts_for(facts)
        if missing:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="INSUFFICIENT_FACTS",
                message="Карточку пока нельзя подтвердить",
                details={"missingFactKeys": [item.fact_key.value for item in missing]},
            )

        case = Case(
            clinic_id=actor.clinic_id,
            created_by_membership_id=actor.membership_id,
            status=CaseStatus.ANALYSIS_BLOCKED.value,
            intake_schema_version=payload.intake_schema_version,
        )
        session.add(case)
        await session.flush()

        fact_rows = [
            CaseFact(
                clinic_id=actor.clinic_id,
                case_id=case.id,
                fact_key=item.fact_key.value,
                revision=1,
                value_type=item.value_type,
                value_json=item.value,
                source_type=item.source_type,
                source_ref_json={"workflowId": str(workflow_id)},
                evidence_status="UNVERIFIED",
                recorded_by_membership_id=actor.membership_id,
                supersedes_fact_id=None,
            )
            for item in payload.facts
        ]
        report_id = uuid4()
        canonical = build_intake_report(
            report_id=report_id,
            case_id=case.id,
            public_number=_case_response(case).public_number,
            case_status=CaseStatus.ANALYSIS_BLOCKED,
            report_version=1,
            generated_at=datetime.now(UTC),
            facts=facts,
            missing_facts=[],
        )
        report_json = canonical.model_dump(mode="json", by_alias=True)
        pdf = render_report_pdf(canonical)
        report_row = CaseReport(
            id=report_id,
            clinic_id=actor.clinic_id,
            case_id=case.id,
            report_version=1,
            schema_version=canonical.schema_version,
            status="ANALYSIS_BLOCKED",
            report_json=report_json,
            content_sha256=_canonical_hash(report_json),
            pdf_bytes=pdf,
            pdf_sha256=hashlib.sha256(pdf).hexdigest(),
            pdf_size_bytes=len(pdf),
            facts_snapshot_sha256=canonical.fact_snapshot_sha256,
            created_by_membership_id=actor.membership_id,
        )
        session.add_all([*fact_rows, report_row])
        await session.flush()

        result = TelegramWorkflowResponse(
            workflowId=workflow_id,
            state="SUCCEEDED",
            case=_case_response(case),
            report=ReportResponse(
                id=report_row.id,
                caseId=report_row.case_id,
                reportVersion=report_row.report_version,
                reportJson=report_row.report_json,
                pdfSha256=report_row.pdf_sha256,
                createdAt=report_row.created_at,
            ),
        )
        workflow = TelegramCaseWorkflow(
            id=workflow_id,
            clinic_id=actor.clinic_id,
            actor_membership_id=actor.membership_id,
            request_sha256=request_hash,
            state="SUCCEEDED",
            case_id=case.id,
            report_id=report_row.id,
        )
        session.add_all(
            [
                workflow,
                _audit(
                    actor=actor,
                    action="CASE_CREATED",
                    resource_type="CASE",
                    resource_id=case.id,
                    metadata={"channel": "TELEGRAM", "schema": payload.intake_schema_version},
                ),
                _audit(
                    actor=actor,
                    action="CASE_FACTS_RECORDED",
                    resource_type="CASE",
                    resource_id=case.id,
                    metadata={"factKeys": [item.fact_key.value for item in payload.facts]},
                ),
                _audit(
                    actor=actor,
                    action="CASE_INTAKE_FINALIZED",
                    resource_type="CASE",
                    resource_id=case.id,
                    metadata={"analysisStatus": "LEGAL_CORPUS_NOT_READY"},
                ),
                _audit(
                    actor=actor,
                    action="CASE_REPORT_CREATED",
                    resource_type="CASE_REPORT",
                    resource_id=report_row.id,
                    metadata={"reportVersion": 1, "schemaVersion": canonical.schema_version},
                ),
                _audit(
                    actor=actor,
                    action="TELEGRAM_WORKFLOW_SUCCEEDED",
                    resource_type="TELEGRAM_WORKFLOW",
                    resource_id=workflow_id,
                    metadata={
                        "caseId": str(case.id),
                        "reportId": str(report_row.id),
                        "schema": payload.intake_schema_version,
                    },
                ),
            ]
        )
        try:
            await session.flush()
        except IntegrityError as exc:
            constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            await session.rollback()
            if constraint_name == "telegram_case_workflows_pkey":
                raise ApiError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="WORKFLOW_ID_UNAVAILABLE",
                    message="Workflow identifier is unavailable",
                ) from exc
            raise
        await session.commit()
        return result

    @router.get(
        "/telegram-case-workflows/{workflow_id}",
        response_model=TelegramWorkflowResponse,
    )
    async def get_telegram_workflow(
        workflow_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> TelegramWorkflowResponse:
        actor = await resolve_actor(session, telegram_user_id)
        workflow = await session.scalar(
            select(TelegramCaseWorkflow).where(
                TelegramCaseWorkflow.id == workflow_id,
                TelegramCaseWorkflow.clinic_id == actor.clinic_id,
                TelegramCaseWorkflow.actor_membership_id == actor.membership_id,
            )
        )
        if workflow is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="WORKFLOW_NOT_FOUND",
                message="Workflow not found",
            )
        return await _workflow_response(session, workflow)

    @router.get("/reports/{report_id}/pdf")
    async def get_report_pdf(
        report_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> StreamingResponse:
        actor = await resolve_actor(session, telegram_user_id)
        report = await session.scalar(
            select(CaseReport).where(
                CaseReport.id == report_id,
                CaseReport.clinic_id == actor.clinic_id,
            )
        )
        if report is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REPORT_NOT_FOUND",
                message="Report not found",
            )
        return StreamingResponse(
            iter([report.pdf_bytes]),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="dental-legal-{report.id}.pdf"',
                "X-Content-SHA256": report.pdf_sha256,
            },
        )

    return router
