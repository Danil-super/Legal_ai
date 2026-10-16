"""Durable early understanding; all transactions end before optional model I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.case_api import (
    CASE_INTAKE_ROLES,
    ActorContext,
    ApiError,
    _audit,
    _canonical_hash,
    resolve_actor,
)
from legal_core.case_preparation_contracts import DraftPreparationIntent
from legal_core.intake_preparation_contracts import DraftSuggestions
from legal_core.models import IdempotencyRecord, SubscriptionEntitlement, TelegramIntakeDraft
from legal_core.pseudonymization import contains_obvious_direct_identifier

OperationKind = Literal["UNDERSTAND_INTAKE", "INTERPRET_ANSWER"]
RESOURCE_TYPE = "INTAKE_PREPARATION"
LEASE_DURATION = timedelta(seconds=180)


@dataclass(frozen=True)
class PreparationOperation:
    operation_id: UUID
    state: Literal["PENDING", "PROPOSED", "MANUAL_CONTINUATION"]
    lease_token: UUID | None = None
    suggestions: DraftSuggestions | None = None


async def _draft(
    session: AsyncSession, actor: ActorContext, draft_id: UUID,
) -> TelegramIntakeDraft:
    if actor.role not in CASE_INTAKE_ROLES:
        raise ApiError(status_code=403, code="CASE_INTAKE_NOT_ALLOWED",
                       message="Intake not allowed")
    draft = await session.scalar(select(TelegramIntakeDraft).where(
        TelegramIntakeDraft.id == draft_id, TelegramIntakeDraft.clinic_id == actor.clinic_id,
        TelegramIntakeDraft.actor_membership_id == actor.membership_id,
        TelegramIntakeDraft.status == "DRAFT",
    ).with_for_update())
    if draft is None:
        raise ApiError(status_code=404, code="INTAKE_DRAFT_NOT_FOUND", message="Draft not found")
    if draft.purge_after <= datetime.now(UTC):
        raise ApiError(status_code=410, code="INTAKE_DRAFT_EXPIRED", message="Draft expired")
    return draft


def _intent(actor: ActorContext, draft: TelegramIntakeDraft, kind: OperationKind
            ) -> DraftPreparationIntent:
    return DraftPreparationIntent(
        clinicId=actor.clinic_id, actorMembershipId=actor.membership_id,
        draftId=draft.id, draftRevision=draft.revision, operationKind=kind,
        inputSha256=_canonical_hash({
            "draftData": draft.draft_json, "wizardState": draft.wizard_state,
        }),
    )


def _source(draft: TelegramIntakeDraft) -> str:
    source = draft.draft_json.get("eventSummary")
    if (
        draft.draft_json.get("intakeVersion") != 2 or not isinstance(source, str)
        or not 10 <= len(source) <= 1500 or contains_obvious_direct_identifier(source)
    ):
        raise ApiError(status_code=422, code="UNDERSTANDING_INPUT_INVALID",
                       message="A bounded de-identified v2 description is required")
    return source


def _result(record: IdempotencyRecord) -> PreparationOperation:
    if record.state == "IN_PROGRESS":
        if record.lease_until is not None and record.lease_until > datetime.now(UTC):
            return PreparationOperation(record.id, "PENDING")
        record.state, record.lease_token, record.lease_until = "FAILED", None, None
    if record.state == "SUCCEEDED" and record.response_json is not None:
        return PreparationOperation(record.id, "PROPOSED",
                                    suggestions=DraftSuggestions.model_validate(
                                        record.response_json["suggestions"],
                                    ))
    return PreparationOperation(record.id, "MANUAL_CONTINUATION")


def _validate_binding(
    actor: ActorContext, draft: TelegramIntakeDraft, record: IdempotencyRecord,
) -> DraftPreparationIntent:
    if record.response_json is None:
        raise ApiError(status_code=410, code="PREPARATION_EXPIRED", message="Operation expired")
    try:
        intent = DraftPreparationIntent.model_validate(record.response_json["intent"])
    except (KeyError, ValueError) as error:
        raise ApiError(status_code=409, code="PREPARATION_INTENT_INVALID",
                       message="Operation intent is not valid") from error
    if (
        intent != _intent(actor, draft, intent.operation_kind) or record.scope != intent.scope
        or record.key != str(intent.idempotency_key)
        or record.request_sha256 != intent.request_sha256
        or record.resource_id != draft.id or record.resource_type != RESOURCE_TYPE
    ):
        raise ApiError(status_code=409, code="PREPARATION_INPUT_STALE", message="Draft has changed")
    return intent


async def _recheck_after_locks(
    session: AsyncSession, telegram_user_id: int, actor: ActorContext, draft: TelegramIntakeDraft,
) -> None:
    if await resolve_actor(session, telegram_user_id) != actor:
        raise ApiError(
            status_code=403, code="ACTOR_CONTEXT_CHANGED", message="Actor access changed",
        )
    # now() is the transaction start; a blocked lock may outlive the entitlement.
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
    if draft.purge_after <= datetime.now(UTC):
        raise ApiError(status_code=410, code="INTAKE_DRAFT_EXPIRED", message="Draft expired")


async def _owned_operation(
    session: AsyncSession, telegram_user_id: int, operation_id: UUID,
) -> tuple[ActorContext, TelegramIntakeDraft, IdempotencyRecord]:
    actor = await resolve_actor(session, telegram_user_id)
    criteria = (
        IdempotencyRecord.id == operation_id, IdempotencyRecord.clinic_id == actor.clinic_id,
        IdempotencyRecord.actor_membership_id == actor.membership_id,
        IdempotencyRecord.resource_type == RESOURCE_TYPE,
    )
    resource_id = await session.scalar(select(IdempotencyRecord.resource_id).where(*criteria))
    if resource_id is None:
        raise ApiError(status_code=404, code="PREPARATION_NOT_FOUND", message="Operation not found")
    # Same lock order as reservation: draft first, then operation. No lock crosses model I/O.
    draft = await _draft(session, actor, resource_id)
    record = await session.scalar(select(IdempotencyRecord).where(*criteria).with_for_update())
    if record is None:
        raise ApiError(status_code=410, code="PREPARATION_EXPIRED", message="Operation expired")
    _validate_binding(actor, draft, record)
    await _recheck_after_locks(session, telegram_user_id, actor, draft)
    return actor, draft, record


async def reserve_preparation(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int,
    draft_id: UUID, expected_revision: int, operation_kind: OperationKind,
) -> PreparationOperation:
    async with sessions() as session:
        actor = await resolve_actor(session, telegram_user_id)
        draft = await _draft(session, actor, draft_id)
        if draft.revision != expected_revision or type(expected_revision) is not int:
            raise ApiError(status_code=409, code="INTAKE_DRAFT_REVISION_CONFLICT",
                           message="Draft revision has changed")
        _source(draft)
        intent = _intent(actor, draft, operation_kind)
        record_id, token = uuid4(), uuid4()
        inserted = await session.scalar(insert(IdempotencyRecord).values(
            id=record_id, clinic_id=actor.clinic_id, actor_membership_id=actor.membership_id,
            scope=intent.scope, key=str(intent.idempotency_key),
            request_sha256=intent.request_sha256,
            resource_type=RESOURCE_TYPE, resource_id=draft.id, state="IN_PROGRESS",
            lease_token=token, lease_until=datetime.now(UTC) + LEASE_DURATION,
            response_json={"intent": intent.model_dump(mode="json", by_alias=True),
                           "suggestions": None},
        ).on_conflict_do_nothing(index_elements=[
            "scope", "actor_membership_id", "key",
        ]).returning(IdempotencyRecord.id))
        record = await session.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.clinic_id == actor.clinic_id,
            IdempotencyRecord.actor_membership_id == actor.membership_id,
            IdempotencyRecord.scope == intent.scope,
            IdempotencyRecord.key == str(intent.idempotency_key),
        ).with_for_update())
        if record is None or record.request_sha256 != intent.request_sha256:
            raise ApiError(status_code=409, code="PREPARATION_INTENT_CONFLICT",
                           message="Operation intent conflict")
        _validate_binding(actor, draft, record)
        await _recheck_after_locks(session, telegram_user_id, actor, draft)
        result = _result(record)
        if result.suggestions is not None:
            result.suggestions.validate_source(_source(draft))
        if inserted is not None:
            result = PreparationOperation(record.id, "PENDING", lease_token=token)
            session.add(_audit(actor=actor, action="INTAKE_PREPARATION_RESERVED",
                               resource_type=RESOURCE_TYPE, resource_id=record.id,
                               metadata={"revision": draft.revision, "kind": operation_kind}))
        await session.commit()
        return result


async def read_preparation(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int, operation_id: UUID,
) -> PreparationOperation:
    async with sessions() as session:
        _, draft, record = await _owned_operation(session, telegram_user_id, operation_id)
        result = _result(record)
        if result.suggestions is not None:
            result.suggestions.validate_source(_source(draft))
        await session.commit()
        return result


async def complete_preparation(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int, operation_id: UUID,
    lease_token: UUID, suggestions: DraftSuggestions,
) -> PreparationOperation:
    async with sessions() as session:
        actor, draft, record = await _owned_operation(session, telegram_user_id, operation_id)
        if (
            record.state != "IN_PROGRESS" or record.lease_token != lease_token
            or record.lease_until is None or record.lease_until <= datetime.now(UTC)
        ):
            raise ApiError(status_code=409, code="PREPARATION_LEASE_EXPIRED",
                           message="Operation lease is no longer current")
        # Python-mode reparsing preserves strict bool/int distinctions before JSON serialization.
        checked = DraftSuggestions.model_validate(suggestions.model_dump(by_alias=True))
        checked.validate_source(_source(draft))
        record.response_json = {
            "intent": _intent(actor, draft, DraftPreparationIntent.model_validate(
                (record.response_json or {})["intent"],
            ).operation_kind).model_dump(mode="json", by_alias=True),
            # Omit absent proposals, not mandatory nested date:null for UNKNOWN.
            "suggestions": {
                key: value for key, value in checked.model_dump(mode="json", by_alias=True).items()
                if value is not None
            },
        }
        record.state, record.lease_token, record.lease_until = "SUCCEEDED", None, None
        session.add(_audit(actor=actor, action="INTAKE_PREPARATION_PROPOSED",
                           resource_type=RESOURCE_TYPE, resource_id=record.id,
                           metadata={"revision": draft.revision,
                                     "fieldCount": len(checked.source_spans)}))
        result = _result(record)
        await session.commit()
        return result


async def fail_preparation(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int, operation_id: UUID,
    lease_token: UUID, reason_code: Literal[
        "MODEL_TIMEOUT", "MODEL_UNAVAILABLE", "MODEL_PROTOCOL_INVALID",
    ],
) -> PreparationOperation:
    async with sessions() as session:
        actor, _, record = await _owned_operation(session, telegram_user_id, operation_id)
        if reason_code not in {"MODEL_TIMEOUT", "MODEL_UNAVAILABLE", "MODEL_PROTOCOL_INVALID"}:
            raise ValueError("unsupported preparation failure reason")
        if (
            record.state != "IN_PROGRESS" or record.lease_token != lease_token
            or record.lease_until is None or record.lease_until <= datetime.now(UTC)
        ):
            raise ApiError(status_code=409, code="PREPARATION_LEASE_EXPIRED",
                           message="Operation lease is no longer current")
        record.state, record.lease_token, record.lease_until = "FAILED", None, None
        session.add(_audit(actor=actor, action="INTAKE_PREPARATION_MANUAL",
                           resource_type=RESOURCE_TYPE, resource_id=record.id,
                           metadata={"reasonCode": reason_code}))
        result = _result(record)
        await session.commit()
        return result
