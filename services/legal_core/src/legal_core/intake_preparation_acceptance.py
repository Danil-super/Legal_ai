"""Explicit operation-bound acceptance; no model, fact, case or analysis side effects."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.api_contracts import TelegramIntakeDraftResponse
from legal_core.case_api import DRAFT_RETENTION, ApiError, _audit, _canonical_hash, _draft_response
from legal_core.case_preparation_contracts import PreparationModel
from legal_core.intake_preparation import (
    RESOURCE_TYPE,
    _acceptance_receipt,
    _checked_result,
    _locked_owned_operation,
    _proposal_metadata,
    _source,
    _validate_binding,
)
from legal_core.intake_preparation_contracts import AcceptUnderstandingRequest
from legal_core.intake_preparation_merge import merge_preparation_suggestions


class AcceptUnderstandingResponse(PreparationModel):
    operation_id: UUID = Field(alias="operationId")
    accepted_revision: int = Field(alias="acceptedRevision", strict=True, ge=1)
    draft: TelegramIntakeDraftResponse


async def accept_preparation(
    sessions: async_sessionmaker[AsyncSession], *, telegram_user_id: int,
    operation_id: UUID, request: AcceptUnderstandingRequest,
) -> AcceptUnderstandingResponse:
    # Reparse Python values, including strict integer types, before touching storage.
    request = AcceptUnderstandingRequest.model_validate(request.model_dump(by_alias=True))
    request_hash = _canonical_hash(request.model_dump(mode="json", by_alias=True))
    async with sessions() as session:
        actor, draft, record = await _locked_owned_operation(
            session, telegram_user_id, operation_id,
        )
        receipt = await _acceptance_receipt(session, actor, draft, record)
        if receipt is not None:
            if receipt["requestSha256"] != request_hash:
                raise ApiError(status_code=409, code="PREPARATION_ACCEPTANCE_CONFLICT",
                               message="Acceptance request differs from the committed request")
            return AcceptUnderstandingResponse(
                operationId=record.id, acceptedRevision=receipt["acceptedRevision"],
                draft=_draft_response(draft),
            )
        intent = _validate_binding(actor, draft, record)
        proposed = await _checked_result(session, actor, draft, record)
        if (intent.operation_kind != "UNDERSTAND_INTAKE" or proposed.state != "PROPOSED"
                or proposed.suggestions is None or proposed.source is None):
            raise ApiError(status_code=409, code="PREPARATION_NOT_PROPOSED",
                           message="There is no current proposal to confirm")
        if (request.expected_revision != draft.revision
                or request.expected_input_sha256 != intent.input_sha256
                or request.expected_proposal_sha256 != proposed.source.proposal_sha256):
            raise ApiError(status_code=409, code="PREPARATION_ACCEPTANCE_CONFLICT",
                           message="The displayed proposal has changed")
        candidate = merge_preparation_suggestions(
            draft.draft_json, proposed.suggestions, _source(draft),
        )
        draft.draft_json = candidate
        draft.revision += 1
        draft.updated_at = datetime.now(UTC)
        draft.purge_after = draft.updated_at + DRAFT_RETENTION
        receipt = {
            "acceptedRevision": draft.revision, "requestSha256": request_hash,
            "proposalSha256": proposed.source.proposal_sha256,
            "outputSha256": _canonical_hash({"draftData": candidate,
                                             "wizardState": draft.wizard_state}),
        }
        record.response_json = {**(record.response_json or {}), "acceptance": receipt}
        session.add(_audit(
            actor=actor, action="INTAKE_PREPARATION_ACCEPTED", resource_type=RESOURCE_TYPE,
            resource_id=record.id, metadata={
                **_proposal_metadata(intent, proposed.suggestions), **receipt,
            },
        ))
        result = AcceptUnderstandingResponse(
            operationId=record.id, acceptedRevision=draft.revision, draft=_draft_response(draft),
        )
        await session.commit()
        return result
