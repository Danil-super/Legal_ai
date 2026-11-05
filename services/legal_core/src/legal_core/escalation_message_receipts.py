"""Pointer-only receipts for committed discussion and resolution operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.models import AuditEvent, CaseEscalationMessage

if TYPE_CHECKING:
    from legal_core.case_api import ActorContext

Action = Literal["discussion", "resolve"]


async def recheck_actor(
    session: AsyncSession, *, actor: ActorContext, telegram_user_id: int,
    escalation_id: UUID, action: Action,
) -> ActorContext:
    from legal_core.case_api import ApiError, _discussion_escalation, resolve_actor

    current = await resolve_actor(session, telegram_user_id)
    if current.clinic_id != actor.clinic_id or current.membership_id != actor.membership_id:
        raise ApiError(status_code=403, code="ACTOR_NOT_AUTHORIZED", message="Actor changed")
    await _discussion_escalation(session, current, escalation_id)
    if action == "resolve" and current.role not in {"CLINIC_OWNER", "CLINIC_LAWYER"}:
        raise ApiError(status_code=403, code="LAWYER_REQUIRED", message="Lawyer role required")
    return current


def _scope(action: Action, escalation_id: UUID) -> str:
    return f"ESCALATION_{action.upper()}:{escalation_id}"


def _request_hash(action: Action, escalation_id: UUID, body: str) -> str:
    from legal_core.case_api import _canonical_hash

    return _canonical_hash({"action": action, "escalationId": str(escalation_id), "body": body})


async def replay_message(
    session: AsyncSession, *, actor: ActorContext, escalation_id: UUID,
    action: Action, key: UUID | None, body: str,
) -> CaseEscalationMessage | None:
    """Call only after current access, retention and case/escalation locks are checked."""
    from legal_core.case_api import ApiError, _idempotency_replay

    if key is None:
        return None
    request_hash = _request_hash(action, escalation_id, body)
    replay = await _idempotency_replay(session, actor=actor, scope=_scope(action, escalation_id),
                                      key=key, request_hash=request_hash)
    if replay is None:
        return None
    try:
        message_id = UUID(replay["messageId"])
    except (KeyError, ValueError, TypeError):
        message_id = None
    message: CaseEscalationMessage | None = None
    if message_id is not None:
        message = await session.scalar(select(CaseEscalationMessage).where(
            CaseEscalationMessage.id == message_id,
            CaseEscalationMessage.clinic_id == actor.clinic_id,
            CaseEscalationMessage.escalation_id == escalation_id,
            CaseEscalationMessage.author_membership_id == actor.membership_id,
        ))
    audit = await session.scalar(select(AuditEvent.id).where(
        AuditEvent.clinic_id == actor.clinic_id,
        AuditEvent.actor_membership_id == actor.membership_id,
        AuditEvent.action == "ESCALATION_OPERATION_RETAINED",
        AuditEvent.resource_id == message_id,
        AuditEvent.metadata_json["requestSha256"].as_string() == request_hash,
        AuditEvent.metadata_json["operationKey"].as_string() == str(key),
    )) if message_id is not None else None
    if message is None or message.body != body or audit is None:
        raise ApiError(status_code=409, code="ESCALATION_OPERATION_INVALID",
                       message="The retained operation cannot be verified")
    return message


def retain_message(
    session: AsyncSession, *, actor: ActorContext, message: CaseEscalationMessage,
    action: Action, key: UUID | None,
) -> None:
    from legal_core.case_api import _audit, _finish_idempotency, _new_idempotency_record

    if key is None:
        return
    request_hash = _request_hash(action, message.escalation_id, message.body)
    record = _new_idempotency_record(actor=actor, scope=_scope(action, message.escalation_id),
                                    key=key, request_hash=request_hash)
    _finish_idempotency(record, resource_type="ESCALATION_MESSAGE", resource_id=message.id,
                        response_json={"messageId": str(message.id)})
    session.add_all([record, _audit(
        actor=actor, action="ESCALATION_OPERATION_RETAINED",
        resource_type="CASE_ESCALATION_MESSAGE", resource_id=message.id,
        metadata={"operationKey": str(key), "requestSha256": request_hash, "action": action},
    )])
