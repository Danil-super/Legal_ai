"""Private metadata-only input resolution; no arbitrary-source model endpoint."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis_api import _require_agent_internal_key
from legal_core.case_api import TelegramUserId
from legal_core.intake_preparation import read_understanding_context
from legal_core.intake_preparation_contracts import (
    IntakeUnderstandingContext,
    UnderstandingContextRequest,
)


def create_intake_preparation_router(sessions: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter()

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
