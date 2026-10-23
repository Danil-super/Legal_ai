"""Metadata-only understanding; Core authorizes source, models never confirm it."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from agent_orchestrator.hermes_client import HermesProtocolError, HermesUnavailable
from agent_orchestrator.intake_understanding import IntakeUnderstanding
from agent_orchestrator.legal_core_client import (
    LegalCoreClient,
    LegalCoreError,
    LegalCoreProtocolError,
)
from legal_core.intake_preparation_contracts import (
    UnderstandingContextRequest,
    UnderstandingInvocation,
    UnderstandingProposal,
)


class _MetadataRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError:
                # Only this new boundary changes; preserve legacy validation responses.
                return JSONResponse(status_code=422, content={
                    "detail": {"code": "UNDERSTANDING_INPUT_INVALID"},
                })
        return handler


def create_understanding_router(
    *, legal_core: LegalCoreClient, understanding: IntakeUnderstanding | None,
    require_internal_key: Callable[[str], None], stage_timeout: float, wall_timeout: float,
) -> APIRouter:
    router = APIRouter(route_class=_MetadataRoute)

    @router.post("/v1/intake-preparations/{operation_id}/understand",
                 response_model=UnderstandingProposal)
    async def understand(
        operation_id: UUID,
        request: UnderstandingInvocation,
        internal_key: Annotated[str, Header(alias="X-Agent-Internal-Key", max_length=512)],
        telegram_user_id: Annotated[int, Header(alias="X-Telegram-User-Id", gt=0)],
    ) -> UnderstandingProposal:
        require_internal_key(internal_key)
        if understanding is None:
            raise HTTPException(status_code=503, detail={"code": "MODEL_UNAVAILABLE"})
        try:
            async with asyncio.timeout(wall_timeout):
                context = await legal_core.get_understanding_context(
                    operation_id=operation_id, draft_id=request.draft_id,
                    telegram_user_id=telegram_user_id,
                    binding=UnderstandingContextRequest(
                        leaseToken=request.lease_token, expectedRevision=request.expected_revision,
                        expectedInputSha256=request.expected_input_sha256,
                    ),
                )
                if (
                    context.operation_id != operation_id or context.draft_id != request.draft_id
                    or context.draft_revision != request.expected_revision
                    or context.input_sha256 != request.expected_input_sha256
                ):
                    raise LegalCoreProtocolError("understanding context binding mismatch")
                async with asyncio.timeout(stage_timeout):
                    suggestions = await understanding.understand(
                        context.source, as_of_date=context.as_of_date,
                    )
                return UnderstandingProposal(
                    operationId=operation_id, draftId=context.draft_id,
                    draftRevision=context.draft_revision, inputSha256=context.input_sha256,
                    suggestions=suggestions,
                )
        except LegalCoreError as error:
            raise HTTPException(status_code=error.status_code,
                                detail={"code": "UNDERSTANDING_CONTEXT_REFUSED"}) from None
        except LegalCoreProtocolError:
            raise HTTPException(status_code=503,
                                detail={"code": "UNDERSTANDING_CONTEXT_UNAVAILABLE"}) from None
        except TimeoutError:
            raise HTTPException(status_code=503, detail={"code": "MODEL_TIMEOUT"}) from None
        except HermesUnavailable:
            raise HTTPException(status_code=503, detail={"code": "MODEL_UNAVAILABLE"}) from None
        except (HermesProtocolError, ValueError, TypeError):
            raise HTTPException(status_code=502,
                                detail={"code": "MODEL_PROTOCOL_INVALID"}) from None

    return router
