"""Tenant-scoped Case Core API backed by PostgreSQL."""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast
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
    ClinicMemberCreateRequest,
    ClinicMemberListResponse,
    ClinicMemberResponse,
    EscalationDiscussionMessageRequest,
    EscalationDiscussionMessageResponse,
    EscalationDiscussionResponse,
    EscalationQueueItemResponse,
    EscalationQueueResponse,
    CreateCaseRequest,
    CreateReportRequest,
    FinalizeRequest,
    IntakeResponse,
    PlatformSubscriptionGrantRequest,
    PlatformSubscriptionGrantResponse,
    ReportResponse,
    TelegramDraftWizardState,
    TelegramIntakeDraftArchiveRequest,
    TelegramIntakeDraftCreateRequest,
    TelegramIntakeDraftListResponse,
    TelegramIntakeDraftResponse,
    TelegramIntakeDraftSummary,
    TelegramIntakeDraftUpdateRequest,
    TelegramWorkflowResponse,
    TelegramWorkflowSubmissionRequest,
)
from legal_core.contracts import CanonicalReport, CaseStatus, FactKey
from legal_core.intake import missing_facts_for
from legal_core.models import (
    AuditEvent,
    Case,
    CaseEscalation,
    CaseEscalationMessage,
    CaseFact,
    CaseReport,
    Clinic,
    ClinicUser,
    IdempotencyRecord,
    SubscriptionEntitlement,
    TelegramCaseWorkflow,
    TelegramIntakeDraft,
    User,
)
from legal_core.reports import build_intake_report, render_report_pdf
from legal_core.pseudonymization import pseudonymize_text
from legal_core.subscription_provisioning import provision_entitlement_in_session

TelegramUserId = Annotated[int, Header(alias="X-Telegram-User-Id", gt=0)]
IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]
FREE_PILOT_SUBSCRIPTION_PLAN = "FREE_PILOT"
NEW_CLINIC_NAME = "Новая стоматология"
DRAFT_RETENTION = timedelta(days=30)
DRAFT_ACTIVE_LIMIT = 20
CLINIC_ACTOR_ROLES = frozenset({"CLINIC_OWNER", "CLINIC_ADMIN", "CLINIC_LAWYER"})
CASE_INTAKE_ROLES = frozenset({"CLINIC_OWNER", "CLINIC_ADMIN"})


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


def _draft_summary(draft: TelegramIntakeDraft) -> TelegramIntakeDraftSummary:
    incident_type = draft.draft_json.get("incident_type")
    return TelegramIntakeDraftSummary(
        id=draft.id,
        wizardState=cast(TelegramDraftWizardState, draft.wizard_state),
        revision=draft.revision,
        incidentType=incident_type if isinstance(incident_type, str) else None,
        updatedAt=draft.updated_at,
    )


def _draft_response(draft: TelegramIntakeDraft) -> TelegramIntakeDraftResponse:
    return TelegramIntakeDraftResponse(
        **_draft_summary(draft).model_dump(mode="python", by_alias=False),
        draftData=draft.draft_json,
        purgeAfter=draft.purge_after,
    )


async def _actor_draft(
    session: AsyncSession, actor: ActorContext, draft_id: UUID
) -> TelegramIntakeDraft:
    draft = await session.scalar(
        select(TelegramIntakeDraft)
        .where(
            TelegramIntakeDraft.id == draft_id,
            TelegramIntakeDraft.clinic_id == actor.clinic_id,
            TelegramIntakeDraft.actor_membership_id == actor.membership_id,
            TelegramIntakeDraft.status == "DRAFT",
        )
        .with_for_update()
    )
    if draft is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="INTAKE_DRAFT_NOT_FOUND",
            message="Intake draft not found",
        )
    return draft


async def resolve_actor(session: AsyncSession, telegram_user_id: int) -> ActorContext:
    result = await session.execute(
        select(User.id, ClinicUser.id, ClinicUser.clinic_id, ClinicUser.role)
        .join(ClinicUser, ClinicUser.user_id == User.id)
        .where(
            User.telegram_user_id == telegram_user_id,
            User.status == "ACTIVE",
            ClinicUser.status == "ACTIVE",
            ClinicUser.role.in_(CLINIC_ACTOR_ROLES),
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
    entitlement = await session.scalar(
        select(SubscriptionEntitlement.id).where(
            SubscriptionEntitlement.clinic_id == clinic_id,
            SubscriptionEntitlement.user_id == user_id,
            SubscriptionEntitlement.status == "ACTIVE",
            SubscriptionEntitlement.starts_at <= func.now(),
            (SubscriptionEntitlement.ends_at.is_(None))
            | (SubscriptionEntitlement.ends_at > func.now()),
        )
    )
    if entitlement is None:
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="SUBSCRIPTION_INACTIVE",
            message="Telegram administrator does not have an active subscription",
        )
    return ActorContext(
        user_id=user_id,
        membership_id=membership_id,
        clinic_id=clinic_id,
        role=role,
    )


def _require_clinic_owner(actor: ActorContext) -> None:
    if actor.role != "CLINIC_OWNER":
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CLINIC_OWNER_REQUIRED",
            message="Clinic owner access is required",
        )


async def _discussion_escalation(
    session: AsyncSession, actor: ActorContext, escalation_id: UUID
) -> CaseEscalation:
    escalation = await session.scalar(
        select(CaseEscalation)
        .join(Case, (Case.clinic_id == CaseEscalation.clinic_id) & (Case.id == CaseEscalation.case_id))
        .where(
            CaseEscalation.id == escalation_id,
            CaseEscalation.clinic_id == actor.clinic_id,
        )
    )
    if escalation is None:
        raise ApiError(status_code=404, code="ESCALATION_NOT_FOUND", message="Escalation not found")
    if actor.role == "CLINIC_ADMIN":
        created_by = await session.scalar(
            select(Case.created_by_membership_id).where(
                Case.clinic_id == actor.clinic_id, Case.id == escalation.case_id
            )
        )
        if created_by != actor.membership_id:
            raise ApiError(status_code=404, code="ESCALATION_NOT_FOUND", message="Escalation not found")
    return escalation


def _configured_platform_owner_telegram_id() -> int:
    raw_value = os.getenv("PLATFORM_OWNER_TELEGRAM_ID", "").strip()
    try:
        owner_id = int(raw_value)
    except ValueError as exc:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="OWNER_CONFIGURATION_INVALID",
            message="Platform owner access is not configured",
        ) from exc
    if owner_id <= 0:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="OWNER_CONFIGURATION_INVALID",
            message="Platform owner access is not configured",
        )
    return owner_id


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
        return value.get("state", value.get("boolean"))
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
        return value.get("state", value.get("boolean"))
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
        actor = await resolve_actor(session, telegram_user_id)
        return ActorResponse(role=actor.role)

    @router.get("/clinic/members", response_model=ClinicMemberListResponse)
    async def list_clinic_members(
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> ClinicMemberListResponse:
        actor = await resolve_actor(session, telegram_user_id)
        _require_clinic_owner(actor)
        rows = list(
            (
                await session.execute(
                    select(User.telegram_user_id, ClinicUser.role)
                    .join(ClinicUser, ClinicUser.user_id == User.id)
                    .where(
                        ClinicUser.clinic_id == actor.clinic_id,
                        ClinicUser.status == "ACTIVE",
                        User.status == "ACTIVE",
                        ClinicUser.role.in_(CLINIC_ACTOR_ROLES),
                    )
                    .order_by(User.telegram_user_id)
                )
            ).all()
        )
        return ClinicMemberListResponse(
            items=[ClinicMemberResponse(telegramUserId=row[0], role=row[1]) for row in rows]
        )

    @router.post(
        "/clinic/members",
        response_model=ClinicMemberResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_clinic_member(
        payload: ClinicMemberCreateRequest,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> ClinicMemberResponse:
        actor = await resolve_actor(session, telegram_user_id)
        _require_clinic_owner(actor)
        await session.execute(select(func.pg_advisory_xact_lock(payload.telegram_user_id)))
        member_user = await session.scalar(
            select(User).where(User.telegram_user_id == payload.telegram_user_id).with_for_update()
        )
        if member_user is None:
            member_user = User(telegram_user_id=payload.telegram_user_id)
            session.add(member_user)
            await session.flush()
        elif member_user.status != "ACTIVE":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="TARGET_USER_INACTIVE",
                message="Target Telegram user is inactive",
            )
        membership = await session.scalar(
            select(ClinicUser)
            .where(ClinicUser.clinic_id == actor.clinic_id, ClinicUser.user_id == member_user.id)
            .with_for_update()
        )
        if membership is None:
            membership = ClinicUser(
                clinic_id=actor.clinic_id,
                user_id=member_user.id,
                role=payload.role,
                status="ACTIVE",
            )
            session.add(membership)
            await session.flush()
        elif membership.role == "CLINIC_OWNER":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="OWNER_ROLE_PROTECTED",
                message="The clinic owner role cannot be changed here",
            )
        else:
            membership.role = payload.role
            membership.status = "ACTIVE"
        await provision_entitlement_in_session(
            session,
            membership_id=membership.id,
            plan_code="CLINIC_MEMBER",
            status="ACTIVE",
            starts_at=datetime.now(UTC),
            ends_at=None,
            performed_by_user_id=actor.user_id,
        )
        session.add(
            _audit(
                actor=actor,
                action="CLINIC_MEMBER_ADDED",
                resource_type="CLINIC_USER",
                resource_id=membership.id,
                metadata={"role": payload.role},
            )
        )
        await session.commit()
        return ClinicMemberResponse(telegramUserId=member_user.telegram_user_id, role=membership.role)

    @router.get(
        "/case-escalations",
        response_model=EscalationQueueResponse,
    )
    async def list_case_escalations(
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> EscalationQueueResponse:
        """List only the de-identified human-review queue visible to this actor."""

        actor = await resolve_actor(session, telegram_user_id)
        statement = (
            select(CaseEscalation, Case)
            .join(
                Case,
                (Case.clinic_id == CaseEscalation.clinic_id)
                & (Case.id == CaseEscalation.case_id),
            )
            .where(CaseEscalation.clinic_id == actor.clinic_id)
            .order_by(CaseEscalation.created_at.desc(), CaseEscalation.id.desc())
            .limit(100)
        )
        if actor.role == "CLINIC_ADMIN":
            statement = statement.where(Case.created_by_membership_id == actor.membership_id)
        rows = list((await session.execute(statement)).all())
        return EscalationQueueResponse(
            items=[
                EscalationQueueItemResponse(
                    escalationId=escalation.id,
                    publicNumber=_case_response(case).public_number,
                    riskLevel=escalation.level,
                    reasonCodes=list(escalation.reason_codes_json),
                    createdAt=escalation.created_at,
                )
                for escalation, case in rows
            ]
        )

    @router.get(
        "/case-escalations/{escalation_id}/discussion",
        response_model=EscalationDiscussionResponse,
    )
    async def get_escalation_discussion(
        escalation_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> EscalationDiscussionResponse:
        actor = await resolve_actor(session, telegram_user_id)
        await _discussion_escalation(session, actor, escalation_id)
        messages = list(
            (
                await session.scalars(
                    select(CaseEscalationMessage)
                    .where(
                        CaseEscalationMessage.clinic_id == actor.clinic_id,
                        CaseEscalationMessage.escalation_id == escalation_id,
                    )
                    .order_by(CaseEscalationMessage.id)
                    .limit(100)
                )
            ).all()
        )
        roles = dict(
            (
                await session.execute(
                    select(ClinicUser.id, ClinicUser.role).where(
                        ClinicUser.clinic_id == actor.clinic_id,
                        ClinicUser.id.in_([message.author_membership_id for message in messages]),
                    )
                )
            ).all()
        )
        return EscalationDiscussionResponse(
            items=[
                EscalationDiscussionMessageResponse(
                    id=message.id,
                    authorRole=roles[message.author_membership_id],
                    body=message.body,
                    createdAt=message.created_at,
                )
                for message in messages
            ]
        )

    @router.post(
        "/case-escalations/{escalation_id}/discussion",
        response_model=EscalationDiscussionMessageResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def post_escalation_discussion_message(
        escalation_id: UUID,
        payload: EscalationDiscussionMessageRequest,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> EscalationDiscussionMessageResponse:
        actor = await resolve_actor(session, telegram_user_id)
        await _discussion_escalation(session, actor, escalation_id)
        if pseudonymize_text(payload.body).changed:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="DIRECT_IDENTIFIER_NOT_ALLOWED",
                message="Discussion messages must not contain direct identifiers",
            )
        message = CaseEscalationMessage(
            clinic_id=actor.clinic_id,
            escalation_id=escalation_id,
            author_membership_id=actor.membership_id,
            body=payload.body,
        )
        session.add(message)
        await session.flush()
        session.add(
            _audit(
                actor=actor,
                action="ESCALATION_DISCUSSION_MESSAGE_CREATED",
                resource_type="CASE_ESCALATION_MESSAGE",
                resource_id=message.id,
                metadata={"bodySha256": hashlib.sha256(message.body.encode()).hexdigest()},
            )
        )
        await session.commit()
        return EscalationDiscussionMessageResponse(
            id=message.id,
            authorRole=actor.role,
            body=message.body,
            createdAt=message.created_at,
        )

    @router.post(
        "/telegram-intake-drafts",
        response_model=TelegramIntakeDraftResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_telegram_intake_draft(
        payload: TelegramIntakeDraftCreateRequest,
        response: Response,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> TelegramIntakeDraftResponse:
        actor = await resolve_actor(session, telegram_user_id)
        if actor.role not in CASE_INTAKE_ROLES:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="CASE_INTAKE_NOT_ALLOWED",
                message="Only clinic administrators may create intake drafts",
            )
        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        replay = await _idempotency_replay(
            session,
            actor=actor,
            scope="telegram-intake-drafts:create",
            key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = status.HTTP_200_OK
            return TelegramIntakeDraftResponse.model_validate(replay)

        active_count = await session.scalar(
            select(func.count())
            .select_from(TelegramIntakeDraft)
            .where(
                TelegramIntakeDraft.clinic_id == actor.clinic_id,
                TelegramIntakeDraft.actor_membership_id == actor.membership_id,
                TelegramIntakeDraft.status == "DRAFT",
            )
        )
        if active_count is not None and active_count >= DRAFT_ACTIVE_LIMIT:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="INTAKE_DRAFT_LIMIT_REACHED",
                message="The active intake draft limit was reached",
            )

        now = datetime.now(UTC)
        draft = TelegramIntakeDraft(
            clinic_id=actor.clinic_id,
            actor_membership_id=actor.membership_id,
            status="DRAFT",
            wizard_state="INCIDENT",
            draft_json={},
            revision=1,
            created_at=now,
            updated_at=now,
            purge_after=now + DRAFT_RETENTION,
        )
        idempotency = _new_idempotency_record(
            actor=actor,
            scope="telegram-intake-drafts:create",
            key=idempotency_key,
            request_hash=request_hash,
        )
        session.add_all([draft, idempotency])
        await session.flush()
        result = _draft_response(draft)
        _finish_idempotency(
            idempotency,
            resource_type="TELEGRAM_INTAKE_DRAFT",
            resource_id=draft.id,
            response_json=result.model_dump(mode="json", by_alias=True),
        )
        session.add(
            _audit(
                actor=actor,
                action="TELEGRAM_INTAKE_DRAFT_CREATED",
                resource_type="TELEGRAM_INTAKE_DRAFT",
                resource_id=draft.id,
                metadata={"wizardState": draft.wizard_state},
            )
        )
        await session.commit()
        return result

    @router.get("/telegram-intake-drafts", response_model=TelegramIntakeDraftListResponse)
    async def list_telegram_intake_drafts(
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> TelegramIntakeDraftListResponse:
        actor = await resolve_actor(session, telegram_user_id)
        drafts = list(
            (
                await session.scalars(
                    select(TelegramIntakeDraft)
                    .where(
                        TelegramIntakeDraft.clinic_id == actor.clinic_id,
                        TelegramIntakeDraft.actor_membership_id == actor.membership_id,
                        TelegramIntakeDraft.status == "DRAFT",
                    )
                    .order_by(TelegramIntakeDraft.updated_at.desc(), TelegramIntakeDraft.id.desc())
                    .limit(DRAFT_ACTIVE_LIMIT)
                )
            ).all()
        )
        return TelegramIntakeDraftListResponse(items=[_draft_summary(draft) for draft in drafts])

    @router.get(
        "/telegram-intake-drafts/{draft_id}", response_model=TelegramIntakeDraftResponse
    )
    async def get_telegram_intake_draft(
        draft_id: UUID,
        telegram_user_id: TelegramUserId,
        session: Session,
    ) -> TelegramIntakeDraftResponse:
        actor = await resolve_actor(session, telegram_user_id)
        draft = await _actor_draft(session, actor, draft_id)
        return _draft_response(draft)

    @router.put(
        "/telegram-intake-drafts/{draft_id}", response_model=TelegramIntakeDraftResponse
    )
    async def update_telegram_intake_draft(
        draft_id: UUID,
        payload: TelegramIntakeDraftUpdateRequest,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> TelegramIntakeDraftResponse:
        actor = await resolve_actor(session, telegram_user_id)
        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        scope = f"telegram-intake-drafts:update:{draft_id}"
        replay = await _idempotency_replay(
            session, actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        if replay is not None:
            return TelegramIntakeDraftResponse.model_validate(replay)

        draft = await _actor_draft(session, actor, draft_id)
        if draft.revision != payload.expected_revision:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="INTAKE_DRAFT_REVISION_CONFLICT",
                message="The intake draft has a newer revision",
            )
        now = datetime.now(UTC)
        draft.wizard_state = payload.wizard_state
        draft.draft_json = payload.draft_data
        draft.revision += 1
        draft.updated_at = now
        draft.purge_after = now + DRAFT_RETENTION
        idempotency = _new_idempotency_record(
            actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        session.add(idempotency)
        await session.flush()
        result = _draft_response(draft)
        _finish_idempotency(
            idempotency,
            resource_type="TELEGRAM_INTAKE_DRAFT",
            resource_id=draft.id,
            response_json=result.model_dump(mode="json", by_alias=True),
        )
        session.add(
            _audit(
                actor=actor,
                action="TELEGRAM_INTAKE_DRAFT_SAVED",
                resource_type="TELEGRAM_INTAKE_DRAFT",
                resource_id=draft.id,
                metadata={"wizardState": draft.wizard_state, "revision": draft.revision},
            )
        )
        await session.commit()
        return result

    @router.post(
        "/telegram-intake-drafts/{draft_id}/archive", response_model=TelegramIntakeDraftResponse
    )
    async def archive_telegram_intake_draft(
        draft_id: UUID,
        payload: TelegramIntakeDraftArchiveRequest,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> TelegramIntakeDraftResponse:
        actor = await resolve_actor(session, telegram_user_id)
        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        scope = f"telegram-intake-drafts:archive:{draft_id}"
        replay = await _idempotency_replay(
            session, actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        if replay is not None:
            return TelegramIntakeDraftResponse.model_validate(replay)

        draft = await _actor_draft(session, actor, draft_id)
        if draft.revision != payload.expected_revision:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="INTAKE_DRAFT_REVISION_CONFLICT",
                message="The intake draft has a newer revision",
            )
        now = datetime.now(UTC)
        draft.status = "ARCHIVED"
        draft.revision += 1
        draft.updated_at = now
        draft.purge_after = now + DRAFT_RETENTION
        idempotency = _new_idempotency_record(
            actor=actor, scope=scope, key=idempotency_key, request_hash=request_hash
        )
        session.add(idempotency)
        await session.flush()
        result = _draft_response(draft)
        _finish_idempotency(
            idempotency,
            resource_type="TELEGRAM_INTAKE_DRAFT",
            resource_id=draft.id,
            response_json=result.model_dump(mode="json", by_alias=True),
        )
        session.add(
            _audit(
                actor=actor,
                action="TELEGRAM_INTAKE_DRAFT_ARCHIVED",
                resource_type="TELEGRAM_INTAKE_DRAFT",
                resource_id=draft.id,
                metadata={"revision": draft.revision},
            )
        )
        await session.commit()
        return result

    @router.post(
        "/platform/subscription-grants",
        response_model=PlatformSubscriptionGrantResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def grant_platform_subscription(
        payload: PlatformSubscriptionGrantRequest,
        response: Response,
        telegram_user_id: TelegramUserId,
        idempotency_key: IdempotencyKey,
        session: Session,
    ) -> PlatformSubscriptionGrantResponse:
        actor = await resolve_actor(session, telegram_user_id)
        if telegram_user_id != _configured_platform_owner_telegram_id():
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="OWNER_REQUIRED",
                message="Platform owner access is required",
            )

        request_hash = _canonical_hash(payload.model_dump(mode="json", by_alias=True))
        replay = await _idempotency_replay(
            session,
            actor=actor,
            scope="platform:subscription-grants",
            key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = status.HTTP_200_OK
            return PlatformSubscriptionGrantResponse.model_validate(replay)

        idempotency = _new_idempotency_record(
            actor=actor,
            scope="platform:subscription-grants",
            key=idempotency_key,
            request_hash=request_hash,
        )
        session.add(idempotency)
        await session.flush()

        # Idempotency keys scope a request; this lock also serializes two distinct owner
        # requests for the same target before a first user/membership is inserted.
        await session.execute(select(func.pg_advisory_xact_lock(payload.telegram_user_id)))
        target_user = await session.scalar(
            select(User)
            .where(User.telegram_user_id == payload.telegram_user_id)
            .with_for_update()
        )
        if target_user is None:
            target_user = User(telegram_user_id=payload.telegram_user_id)
            session.add(target_user)
            await session.flush()
        elif target_user.status != "ACTIVE":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="TARGET_USER_INACTIVE",
                message="Target Telegram user is inactive",
            )

        memberships = list(
            (
                await session.scalars(
                    select(ClinicUser)
                    .where(
                        ClinicUser.user_id == target_user.id,
                        ClinicUser.status == "ACTIVE",
                        ClinicUser.role == "CLINIC_ADMIN",
                    )
                    .with_for_update()
                )
            ).all()
        )
        if len(memberships) > 1:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="TARGET_ADMIN_AMBIGUOUS",
                message="Target Telegram user has multiple active clinic administrator memberships",
            )
        if memberships:
            membership = memberships[0]
            clinic_name = await session.scalar(
                select(Clinic.name).where(Clinic.id == membership.clinic_id)
            )
            if clinic_name is None:
                raise RuntimeError("active clinic administrator membership has no clinic")
        else:
            clinic = Clinic(name=NEW_CLINIC_NAME)
            session.add(clinic)
            await session.flush()
            membership = ClinicUser(
                clinic_id=clinic.id,
                user_id=target_user.id,
                role="CLINIC_ADMIN",
            )
            session.add(membership)
            await session.flush()
            clinic_name = clinic.name

        starts_at = datetime.now(UTC)
        if payload.plan_code == FREE_PILOT_SUBSCRIPTION_PLAN:
            if payload.pilot_days is None:  # pragma: no cover - API contract rejects this.
                raise RuntimeError("validated free-pilot request has no duration")
            ends_at = starts_at + timedelta(days=payload.pilot_days)
        else:
            ends_at = None
        entitlement_id = await provision_entitlement_in_session(
            session,
            membership_id=membership.id,
            plan_code=payload.plan_code,
            status="ACTIVE",
            starts_at=starts_at,
            ends_at=ends_at,
            performed_by_user_id=actor.user_id,
        )
        await session.execute(
            select(func.set_config("app.current_clinic_id", str(actor.clinic_id), True))
        )
        result = PlatformSubscriptionGrantResponse(
            telegramUserId=payload.telegram_user_id,
            clinicName=clinic_name,
            planCode=payload.plan_code,
            status="ACTIVE",
            endsAt=ends_at,
        )
        _finish_idempotency(
            idempotency,
            resource_type="SUBSCRIPTION_ENTITLEMENT",
            resource_id=entitlement_id,
            response_json=result.model_dump(mode="json", by_alias=True),
        )
        await session.commit()
        return result

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
