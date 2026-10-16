"""Private metadata-only input resolution; no arbitrary-source model endpoint."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis_api import _require_agent_internal_key
from legal_core.analysis_safe_stop import require_analysis_running
from legal_core.case_api import TelegramUserId
from legal_core.intake_preparation import (
    PreparationOperation,
    cancel_preparation,
    discover_preparation,
    fail_preparation,
    read_preparation,
    read_understanding_context,
    reserve_preparation,
)
from legal_core.intake_preparation_acceptance import AcceptUnderstandingResponse, accept_preparation
from legal_core.intake_preparation_contracts import (
    AcceptUnderstandingRequest,
    IntakeUnderstandingContext,
    PreparationStatusResponse,
    StartUnderstandingRequest,
    UnderstandingContextRequest,
    UnderstandingDiscoveryResponse,
)
from legal_core.intake_preparation_runner import IntakePreparationRunner


def _public(operation: PreparationOperation) -> PreparationStatusResponse:
    return PreparationStatusResponse(operationId=operation.operation_id,
                                     state=operation.state, suggestions=operation.suggestions,
                                     source=operation.source, acceptance=operation.acceptance)


def create_intake_preparation_router(sessions: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/telegram-intake-drafts/{draft_id}/understanding",
                response_model=UnderstandingDiscoveryResponse)
    async def discover_understanding(
        draft_id: UUID, telegram_user_id: TelegramUserId,
    ) -> UnderstandingDiscoveryResponse:
        operation = await discover_preparation(
            sessions, telegram_user_id=telegram_user_id, draft_id=draft_id,
        )
        return UnderstandingDiscoveryResponse(operation=_public(operation) if operation else None)

    @router.post("/v1/intake-preparations/{operation_id}/acceptance",
                 response_model=AcceptUnderstandingResponse)
    async def accept_understanding(
        operation_id: UUID, request: AcceptUnderstandingRequest, telegram_user_id: TelegramUserId,
    ) -> AcceptUnderstandingResponse:
        return await accept_preparation(
            sessions, telegram_user_id=telegram_user_id, operation_id=operation_id, request=request,
        )

    @router.post("/v1/telegram-intake-drafts/{draft_id}/understanding",
                 response_model=PreparationStatusResponse, status_code=202)
    async def start_understanding(
        draft_id: UUID, request: StartUnderstandingRequest, http_request: Request,
        telegram_user_id: TelegramUserId,
    ) -> PreparationStatusResponse:
        require_analysis_running()
        operation = await reserve_preparation(
            sessions, telegram_user_id=telegram_user_id, draft_id=draft_id,
            expected_revision=request.expected_revision, operation_kind="UNDERSTAND_INTAKE",
        )
        if operation.lease_token is not None:
            runner: IntakePreparationRunner | None = (
                http_request.app.state.intake_preparation_runner
            )
            if runner is None:
                await fail_preparation(
                    sessions, telegram_user_id=telegram_user_id,
                    operation_id=operation.operation_id, lease_token=operation.lease_token,
                    reason_code="MODEL_UNAVAILABLE",
                )
            else:
                await runner.submit(telegram_user_id=telegram_user_id, operation=operation)
        return _public(await read_preparation(
            sessions, telegram_user_id=telegram_user_id, operation_id=operation.operation_id,
        ))

    @router.get("/v1/intake-preparations/{operation_id}", response_model=PreparationStatusResponse)
    async def preparation_status(
        operation_id: UUID, telegram_user_id: TelegramUserId,
    ) -> PreparationStatusResponse:
        return _public(await read_preparation(
            sessions, telegram_user_id=telegram_user_id, operation_id=operation_id,
        ))

    @router.delete("/v1/intake-preparations/{operation_id}",
                   response_model=PreparationStatusResponse)
    async def cancel_understanding(
        operation_id: UUID, http_request: Request, telegram_user_id: TelegramUserId,
    ) -> PreparationStatusResponse:
        operation = await cancel_preparation(
            sessions, telegram_user_id=telegram_user_id, operation_id=operation_id,
        )
        runner: IntakePreparationRunner | None = http_request.app.state.intake_preparation_runner
        if runner is not None:
            runner.cancel(operation_id)
        return _public(operation)

    @router.post(
        "/v1/intake-preparations/{operation_id}/context",
        response_model=IntakeUnderstandingContext,
        dependencies=[Depends(_require_agent_internal_key)],
    )
    async def understanding_context(
        operation_id: UUID,
        request: UnderstandingContextRequest,
        telegram_user_id: TelegramUserId,
    ) -> IntakeUnderstandingContext:
        return await read_understanding_context(
            sessions, telegram_user_id=telegram_user_id, operation_id=operation_id,
            lease_token=request.lease_token, expected_revision=request.expected_revision,
            expected_input_sha256=request.expected_input_sha256,
        )

    return router
