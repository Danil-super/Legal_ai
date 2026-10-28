"""Atomic explicit answer consent; no case, fact snapshot or analysis side effects."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.case_api import DRAFT_RETENTION, ApiError, _audit, _canonical_hash, _draft_response
from legal_core.intake_answer_operations import (
    acceptance_receipt,
    checked_proposal,
    input_hash,
    stored_answer,
)
from legal_core.intake_factual_candidates import apply_factual_answer
from legal_core.intake_preparation import RESOURCE_TYPE, _locked_owned_operation
from legal_core.intake_preparation_acceptance import AcceptUnderstandingResponse
from legal_core.intake_preparation_contracts import AcceptUnderstandingRequest


async def accept_answer(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int,
    operation_id: UUID, request: AcceptUnderstandingRequest,
) -> AcceptUnderstandingResponse:
    request = AcceptUnderstandingRequest.model_validate(request.model_dump(by_alias=True))
    request_hash = _canonical_hash(request.model_dump(mode="json", by_alias=True))
    async with sessions() as session:
        actor, draft, record = await _locked_owned_operation(
            session, telegram_user_id, operation_id,
        )
        historical = stored_answer(actor, draft, record, fresh=False)
        receipt = await acceptance_receipt(session, actor, draft, record, historical)
        if receipt is not None:
            if receipt["requestSha256"] != request_hash:
                raise ApiError(status_code=409, code="ANSWER_ACCEPTANCE_CONFLICT",
                               message="Acceptance differs from the committed request")
            return AcceptUnderstandingResponse(
                operationId=record.id, acceptedRevision=receipt["acceptedRevision"],
                draft=_draft_response(draft),
            )
        bound = stored_answer(actor, draft, record)
        metadata = await checked_proposal(session, actor, record, bound, draft)
        interpretation = bound.interpretation
        if interpretation is None or interpretation.status != "PROPOSED":
            raise ApiError(status_code=409, code="ANSWER_NOT_PROPOSED",
                           message="There is no current factual answer to confirm")
        if (request.expected_revision != draft.revision
                or request.expected_input_sha256 != bound.intent.input_sha256
                or request.expected_proposal_sha256 != metadata["proposalSha256"]):
            raise ApiError(status_code=409, code="ANSWER_ACCEPTANCE_CONFLICT",
                           message="The displayed answer has changed")
        candidate = None
        try:
            candidate = apply_factual_answer(
                draft.draft_json, bound.question, interpretation, bound.answer,
                bound.retained_sources or [],
                as_of_date=bound.intent.as_of_date,
            )
        except (ValueError, TypeError):
            candidate = None
        if candidate is None:
            raise ApiError(status_code=409, code="ANSWER_PROPOSAL_INVALID",
                           message="Answer cannot be applied to this draft")
        draft.draft_json = candidate
        draft.revision += 1
        draft.updated_at = datetime.now(UTC)
        draft.purge_after = draft.updated_at + DRAFT_RETENTION
        receipt = {
            "acceptedRevision": draft.revision, "requestSha256": request_hash,
            "proposalSha256": metadata["proposalSha256"], "outputSha256": input_hash(draft),
        }
        record.response_json = {**(record.response_json or {}), "acceptance": receipt}
        session.add(_audit(actor=actor, action="INTAKE_ANSWER_ACCEPTED",
                           resource_type=RESOURCE_TYPE, resource_id=record.id,
                           metadata={**metadata, **receipt}))
        result = AcceptUnderstandingResponse(
            operationId=record.id, acceptedRevision=draft.revision, draft=_draft_response(draft),
        )
        await session.commit()
        return result
