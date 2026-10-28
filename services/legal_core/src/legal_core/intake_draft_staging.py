"""Provisional owned draft writes; only final fabula confirmation creates user facts."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.api_contracts import TelegramIntakeDraftResponse
from legal_core.case_api import (
    DRAFT_RETENTION,
    ActorContext,
    ApiError,
    _audit,
    _canonical_hash,
    _draft_response,
)
from legal_core.case_preparation_contracts import PreparationModel
from legal_core.intake_answer_operations import checked_proposal, input_hash, stored_answer
from legal_core.intake_factual_candidates import apply_factual_answer
from legal_core.intake_preparation import (
    RESOURCE_TYPE,
    _checked_result,
    _locked_owned_operation,
    _proposal_metadata,
    _source,
    _stored_intent,
    _validate_binding,
)
from legal_core.intake_preparation_contracts import AcceptUnderstandingRequest, DraftSuggestions
from legal_core.intake_preparation_merge import merge_preparation_suggestions
from legal_core.models import AuditEvent, IdempotencyRecord, TelegramIntakeDraft

Kind = Literal["UNDERSTAND_INTAKE", "INTERPRET_ANSWER"]
ACTION = "INTAKE_PROPOSAL_STAGED"


class StageDraftResponse(PreparationModel):
    operation_id: UUID = Field(alias="operationId")
    staged_revision: int = Field(alias="stagedRevision", strict=True, ge=1)
    draft: TelegramIntakeDraftResponse


def conflict() -> ApiError:
    return ApiError(status_code=409, code="INTAKE_STAGING_CONFLICT",
                    message="Provisional draft or proposal has changed")


async def historical_metadata(
    session: AsyncSession, actor: ActorContext, draft: TelegramIntakeDraft,
    record: IdempotencyRecord, kind: Kind,
) -> dict[str, Any]:
    """Validate immutable ownership/proposal before replay, not current source freshness."""
    if (record.state != "SUCCEEDED" or record.lease_token is not None
            or record.lease_until is not None or (record.response_json or {}).get("acceptance")):
        raise conflict()
    if kind == "INTERPRET_ANSWER":
        bound = stored_answer(actor, draft, record, fresh=False)
        if bound.interpretation is None or not bound.interpretation.has_provisional_facts:
            raise conflict()
        return await checked_proposal(session, actor, record, bound)
    intent = _stored_intent(actor, draft, record)
    if intent.operation_kind != kind:
        raise conflict()
    metadata = None
    try:
        proposal = DraftSuggestions.model_validate((record.response_json or {})["suggestions"])
        metadata = _proposal_metadata(intent, proposal)
    except (KeyError, TypeError, ValueError):
        pass
    if metadata is None:
        raise conflict()
    audits = (await session.scalars(select(AuditEvent.metadata_json).where(
        AuditEvent.clinic_id == actor.clinic_id,
        AuditEvent.actor_membership_id == actor.membership_id,
        AuditEvent.resource_type == RESOURCE_TYPE, AuditEvent.resource_id == record.id,
        AuditEvent.action == "INTAKE_PREPARATION_PROPOSED",
    ))).all()
    if len(audits) != 1 or audits[0] != metadata:
        raise conflict()
    return metadata


async def replay_revision(
    session: AsyncSession, actor: ActorContext, draft: TelegramIntakeDraft,
    record: IdempotencyRecord, request: AcceptUnderstandingRequest, kind: Kind,
    metadata: dict[str, Any],
) -> int | None:
    receipts = (await session.scalars(select(AuditEvent.metadata_json).where(
        AuditEvent.clinic_id == actor.clinic_id,
        AuditEvent.actor_membership_id == actor.membership_id,
        AuditEvent.resource_type == RESOURCE_TYPE, AuditEvent.resource_id == record.id,
        AuditEvent.action == ACTION,
    ))).all()
    if not receipts:
        return None
    if len(receipts) != 1:
        raise conflict()
    receipt = receipts[0]
    valid = False
    try:
        revision = receipt["stagedRevision"]
        output_hash = receipt["outputSha256"]
        expected = {
            **metadata, "kind": kind, "stagedRevision": revision,
            "requestSha256": _canonical_hash(request.model_dump(mode="json", by_alias=True)),
            "outputSha256": output_hash, "requiresFinalConfirmation": True,
        }
        selector = AcceptUnderstandingRequest(
            expectedRevision=metadata["revision"], expectedInputSha256=metadata["inputSha256"],
            expectedProposalSha256=metadata["proposalSha256"],
        )
        valid = (
            request == selector and receipt == expected
            and receipt["requiresFinalConfirmation"] is True
            and type(revision) is int and revision == metadata["revision"] + 1
            and draft.revision >= revision
            and isinstance(output_hash, str) and len(output_hash) == 64
            and all(character in "0123456789abcdef" for character in output_hash)
            and (draft.revision > revision or input_hash(draft) == output_hash)
        )
    except (KeyError, TypeError, ValueError):
        pass
    if not valid:
        raise conflict()
    return int(revision)


async def stage(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int, operation_id: UUID,
    request: AcceptUnderstandingRequest, kind: Kind,
) -> StageDraftResponse:
    request = AcceptUnderstandingRequest.model_validate(request.model_dump(by_alias=True))
    async with sessions() as session:
        actor, draft, record = await _locked_owned_operation(
            session, telegram_user_id, operation_id,
        )
        metadata = await historical_metadata(session, actor, draft, record, kind)
        replay = await replay_revision(session, actor, draft, record, request, kind, metadata)
        if replay is not None:
            return StageDraftResponse(operationId=record.id, stagedRevision=replay,
                                      draft=_draft_response(draft))
        if (request.expected_revision != draft.revision
                or request.expected_input_sha256 != metadata["inputSha256"]
                or request.expected_proposal_sha256 != metadata["proposalSha256"]):
            raise conflict()
        candidate: dict[str, object] | None
        if kind == "UNDERSTAND_INTAKE":
            _validate_binding(actor, draft, record)
            proposed = await _checked_result(session, actor, draft, record)
            if proposed.state != "PROPOSED" or proposed.suggestions is None:
                raise conflict()
            candidate = merge_preparation_suggestions(
                draft.draft_json, proposed.suggestions, _source(draft),
            )
        else:
            bound = stored_answer(actor, draft, record)
            await checked_proposal(session, actor, record, bound, draft)
            candidate = None
            try:
                if bound.interpretation is not None:
                    candidate = apply_factual_answer(
                        draft.draft_json, bound.question, bound.interpretation, bound.answer,
                        bound.retained_sources or [],
                        as_of_date=bound.intent.as_of_date,
                    )
            except (ValueError, TypeError):
                pass
            if candidate is None:
                raise conflict()
        draft.draft_json = candidate
        draft.revision += 1
        draft.updated_at = datetime.now(UTC)
        draft.purge_after = draft.updated_at + DRAFT_RETENTION
        session.add(_audit(
            actor=actor, action=ACTION, resource_type=RESOURCE_TYPE, resource_id=record.id,
            metadata={
                **metadata, "kind": kind, "stagedRevision": draft.revision,
                "requestSha256": _canonical_hash(request.model_dump(mode="json", by_alias=True)),
                "outputSha256": input_hash(draft), "requiresFinalConfirmation": True,
            },
        ))
        result = StageDraftResponse(operationId=record.id, stagedRevision=draft.revision,
                                    draft=_draft_response(draft))
        await session.commit()
        return result


async def stage_preparation(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int,
    operation_id: UUID, request: AcceptUnderstandingRequest,
) -> StageDraftResponse:
    return await stage(sessions, telegram_user_id=telegram_user_id, operation_id=operation_id,
                       request=request, kind="UNDERSTAND_INTAKE")


async def stage_answer(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int,
    operation_id: UUID, request: AcceptUnderstandingRequest,
) -> StageDraftResponse:
    return await stage(sessions, telegram_user_id=telegram_user_id, operation_id=operation_id,
                       request=request, kind="INTERPRET_ANSWER")
