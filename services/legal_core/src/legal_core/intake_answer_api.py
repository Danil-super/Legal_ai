"""Owned factual answer operations; no client-selected question or model authority."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis_api import _require_agent_internal_key
from legal_core.case_api import TelegramUserId
from legal_core.intake_answer_acceptance import accept_answer
from legal_core.intake_answer_lifecycle import fail_answer, read_answer_context
from legal_core.intake_answer_operations import (
    cancel_answer,
    discover_answer,
    read_answer,
    reserve_answer,
)
from legal_core.intake_answer_wire import (
    AnswerContext,
    AnswerDiscoveryResponse,
    AnswerStatusResponse,
    StartAnswerRequest,
)
from legal_core.intake_draft_staging import StageDraftResponse, stage_answer
from legal_core.intake_preparation_acceptance import AcceptUnderstandingResponse
from legal_core.intake_preparation_contracts import (
    AcceptUnderstandingRequest,
    UnderstandingContextRequest,
)


def create_answer_router(sessions: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/telegram-intake-drafts/{draft_id}/answer-interpretations",
                 status_code=202, response_model=AnswerStatusResponse)
    async def start(
        draft_id: UUID, request: StartAnswerRequest, http: Request, actor: TelegramUserId,
    ) -> AnswerStatusResponse:
        operation = await reserve_answer(sessions, telegram_user_id=actor, draft_id=draft_id,
                                         expected_revision=request.expected_revision,
                                         answer=request.answer)
        runner = http.app.state.intake_preparation_runner
        if operation.lease_token is not None:
            if runner is None:
                await fail_answer(sessions, telegram_user_id=actor,
                                  operation_id=operation.operation_id,
                                  lease_token=operation.lease_token,
                                  reason_code="MODEL_UNAVAILABLE")
            else:
                await runner.submit(telegram_user_id=actor, operation=operation)
        return (await read_answer(sessions, telegram_user_id=actor,
                                  operation_id=operation.operation_id)).response

    @router.get("/v1/telegram-intake-drafts/{draft_id}/answer-interpretations",
                response_model=AnswerDiscoveryResponse)
    async def discover(draft_id: UUID, actor: TelegramUserId) -> AnswerDiscoveryResponse:
        operation = await discover_answer(sessions, telegram_user_id=actor, draft_id=draft_id)
        return AnswerDiscoveryResponse(operation=operation.response if operation else None)

    @router.get("/v1/intake-answer-preparations/{operation_id}",
                response_model=AnswerStatusResponse)
    async def read(operation_id: UUID, actor: TelegramUserId) -> AnswerStatusResponse:
        return (await read_answer(sessions, telegram_user_id=actor,
                                  operation_id=operation_id)).response

    @router.delete("/v1/intake-answer-preparations/{operation_id}",
                   response_model=AnswerStatusResponse)
    async def cancel(
        operation_id: UUID, http: Request, actor: TelegramUserId,
    ) -> AnswerStatusResponse:
        result = await cancel_answer(sessions, telegram_user_id=actor, operation_id=operation_id)
        runner = http.app.state.intake_preparation_runner
        if runner is not None:
            runner.cancel(operation_id)
        return result.response

    @router.post("/v1/intake-answer-preparations/{operation_id}/acceptance",
                 response_model=AcceptUnderstandingResponse)
    async def accept(operation_id: UUID, request: AcceptUnderstandingRequest,
                     actor: TelegramUserId) -> AcceptUnderstandingResponse:
        return await accept_answer(sessions, telegram_user_id=actor,
                                   operation_id=operation_id, request=request)

    @router.post("/v1/intake-answer-preparations/{operation_id}/draft-staging",
                 response_model=StageDraftResponse)
    async def stage(operation_id: UUID, request: AcceptUnderstandingRequest,
                    actor: TelegramUserId) -> StageDraftResponse:
        return await stage_answer(sessions, telegram_user_id=actor,
                                  operation_id=operation_id, request=request)

    @router.post("/v1/intake-answer-preparations/{operation_id}/context",
                 response_model=AnswerContext, dependencies=[Depends(_require_agent_internal_key)])
    async def context(operation_id: UUID, request: UnderstandingContextRequest,
                      actor: TelegramUserId) -> AnswerContext:
        return await read_answer_context(
            sessions, telegram_user_id=actor, operation_id=operation_id,
            lease_token=request.lease_token, expected_revision=request.expected_revision,
            expected_input_sha256=request.expected_input_sha256,
        )

    return router
