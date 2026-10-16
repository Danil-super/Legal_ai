"""Internal FastAPI service coordinating Legal Core and two isolated Hermes profiles."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict
from uuid import UUID

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from agent_orchestrator.hermes_client import (
    HermesClient,
    HermesEndpoint,
    HermesProtocolError,
    HermesUnavailable,
)
from agent_orchestrator.intake_answer_interpretation import AnswerUnderstanding
from agent_orchestrator.intake_understanding import IntakeUnderstanding
from agent_orchestrator.intake_understanding_api import create_understanding_router
from agent_orchestrator.legal_core_client import (
    LegalCoreClient,
    LegalCoreEndpoint,
    LegalCoreError,
    LegalCoreProtocolError,
)
from agent_orchestrator.projection import build_projection_from_context
from agent_orchestrator.reasoning import LegalReasoningOrchestrator
from legal_core.analysis_contracts import AnalysisSubmissionResponse, PreparationResult
from legal_core.intake_answer_wire import AnswerContext, AnswerProposal
from legal_core.intake_preparation_contracts import (
    UnderstandingContextRequest,
    UnderstandingInvocation,
)

AnalysisResponse = AnalysisSubmissionResponse | PreparationResult

InternalKeyHeader = Annotated[
    str,
    Header(alias="X-Agent-Internal-Key", min_length=16, max_length=512),
]
TelegramUserIdHeader = Annotated[int, Header(alias="X-Telegram-User-Id", gt=0)]
IdempotencyKeyHeader = Annotated[UUID, Header(alias="Idempotency-Key")]
logger = logging.getLogger(__name__)
# One shared budget leaves margins inside the worker's unchanged HTTP 120 s,
# wall 125 s and fenced lease 180 s. HTTP operation timeouts are not total walls.
ANALYSIS_WALL_TIMEOUT_SECONDS = 115
HERMES_STAGE_TIMEOUT_SECONDS = 65


class _AnswerMetadataRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={
                    "detail": {"code": "ANSWER_INPUT_INVALID"},
                })
        return handler


class JobLeaseHeaders(TypedDict, total=False):
    job_id: UUID | None
    job_token: UUID | None


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    internal_key: str
    legal_core_url: str
    hermes_researcher_url: str
    hermes_researcher_key: str
    hermes_researcher_model: str
    hermes_reviewer_url: str
    hermes_reviewer_key: str
    hermes_reviewer_model: str

    def __post_init__(self) -> None:
        if len(self.internal_key) < 32:
            raise ValueError("AGENT_INTERNAL_KEY must contain at least 32 characters")

    @classmethod
    def from_environment(cls) -> ServiceSettings:
        settings = cls(
            internal_key=os.getenv("AGENT_INTERNAL_KEY", ""),
            legal_core_url=os.getenv("LEGAL_CORE_URL", ""),
            hermes_researcher_url=os.getenv("HERMES_RESEARCHER_URL", ""),
            hermes_researcher_key=os.getenv("HERMES_RESEARCHER_API_KEY", ""),
            hermes_researcher_model=os.getenv("HERMES_RESEARCHER_MODEL", "researcher"),
            hermes_reviewer_url=os.getenv("HERMES_REVIEWER_URL", ""),
            hermes_reviewer_key=os.getenv("HERMES_REVIEWER_API_KEY", ""),
            hermes_reviewer_model=os.getenv("HERMES_REVIEWER_MODEL", "reviewer"),
        )
        required = {
            "LEGAL_CORE_URL": settings.legal_core_url,
            "HERMES_RESEARCHER_URL": settings.hermes_researcher_url,
            "HERMES_RESEARCHER_API_KEY": settings.hermes_researcher_key,
            "HERMES_RESEARCHER_MODEL": settings.hermes_researcher_model,
            "HERMES_REVIEWER_URL": settings.hermes_reviewer_url,
            "HERMES_REVIEWER_API_KEY": settings.hermes_reviewer_key,
            "HERMES_REVIEWER_MODEL": settings.hermes_reviewer_model,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"missing agent orchestrator settings: {', '.join(missing)}")
        return settings


@dataclass(frozen=True, slots=True)
class ServiceDependencies:
    legal_core: LegalCoreClient
    reasoning: LegalReasoningOrchestrator
    understanding: IntakeUnderstanding | None = None
    answer_understanding: AnswerUnderstanding | None = None


def build_dependencies(settings: ServiceSettings) -> ServiceDependencies:
    legal_core = LegalCoreClient(
        LegalCoreEndpoint(
            base_url=settings.legal_core_url,
            internal_key=settings.internal_key,
            timeout_seconds=60,
        )
    )
    researcher = HermesClient(
        HermesEndpoint(
            base_url=settings.hermes_researcher_url,
            api_key=settings.hermes_researcher_key,
            model=settings.hermes_researcher_model,
            timeout_seconds=HERMES_STAGE_TIMEOUT_SECONDS,
            stream_response=True,
        )
    )
    reviewer = HermesClient(
        HermesEndpoint(
            base_url=settings.hermes_reviewer_url,
            api_key=settings.hermes_reviewer_key,
            model=settings.hermes_reviewer_model,
            timeout_seconds=HERMES_STAGE_TIMEOUT_SECONDS,
            stream_response=True,
        )
    )
    return ServiceDependencies(
        legal_core=legal_core,
        reasoning=LegalReasoningOrchestrator(researcher=researcher, reviewer=reviewer),
        understanding=IntakeUnderstanding(researcher=researcher),
        answer_understanding=AnswerUnderstanding(researcher=researcher),
    )


def create_app(
    *,
    settings: ServiceSettings,
    dependencies: ServiceDependencies,
) -> FastAPI:
    app = FastAPI(
        title="Dental Legal AI — Agent Orchestrator",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def require_internal_key(value: str) -> None:
        if not hmac.compare_digest(value, settings.internal_key):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "service": "agent-orchestrator"}

    @app.post(
        "/v1/cases/{case_id}/analyze",
        response_model=AnalysisResponse,
    )
    async def analyze_case(
        case_id: UUID,
        x_agent_internal_key: InternalKeyHeader,
        telegram_user_id: TelegramUserIdHeader,
        idempotency_key: IdempotencyKeyHeader,
        x_analysis_job_id: Annotated[UUID | None, Header()] = None,
        x_analysis_job_token: Annotated[UUID | None, Header()] = None,
        x_analysis_result_version: Annotated[Literal["1", "2"], Header()] = "1",
    ) -> AnalysisResponse:
        require_internal_key(x_agent_internal_key)
        if (x_analysis_job_id is not None or x_analysis_job_token is not None) and (
            x_analysis_job_id is None
            or x_analysis_job_token is None
            or x_analysis_job_id != idempotency_key
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "ANALYSIS_JOB_LEASE_EXPIRED"},
            )
        try:
            async with asyncio.timeout(ANALYSIS_WALL_TIMEOUT_SECONDS):
                job_headers: JobLeaseHeaders = (
                    {"job_id": x_analysis_job_id, "job_token": x_analysis_job_token}
                    if x_analysis_job_id is not None else {}
                )
                if x_analysis_result_version == "2":
                    initial = await dependencies.legal_core.prepare_analysis_context(
                        case_id=case_id, telegram_user_id=telegram_user_id,
                        idempotency_key=idempotency_key, **job_headers,
                    )
                    if isinstance(initial, PreparationResult):
                        if initial.root.source.case_id != case_id:
                            raise LegalCoreProtocolError("preparation result case mismatch")
                        return initial
                    context = initial
                else:
                    context = await dependencies.legal_core.get_analysis_context(
                        case_id=case_id, telegram_user_id=telegram_user_id,
                    )
                if context.case_id != case_id:
                    raise LegalCoreProtocolError("analysis context case mismatch")
                projection = build_projection_from_context(context)
                reasoning = await dependencies.reasoning.reason(projection)
                if x_analysis_result_version == "2":
                    prepared = await dependencies.legal_core.submit_reasoning(
                        context=context, reasoning=reasoning, telegram_user_id=telegram_user_id,
                        idempotency_key=idempotency_key, result_version=2, **job_headers,
                    )
                    if not isinstance(prepared, PreparationResult):
                        raise LegalCoreProtocolError("preparation result version mismatch")
                    return prepared
                return await dependencies.legal_core.submit_reasoning(
                    context=context,
                    reasoning=reasoning,
                    telegram_user_id=telegram_user_id,
                    idempotency_key=idempotency_key,
                    **job_headers,
                )
        except TimeoutError:
            logger.warning(json.dumps({
                "event": "analysis_deadline_exceeded", "entryPoint": "analyze",
                "requestId": str(idempotency_key),
            }, sort_keys=True))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "ANALYSIS_PROVIDER_UNAVAILABLE"},
            ) from None
        except LegalCoreError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except HermesUnavailable as exc:
            logger.warning(json.dumps({
                "event": "hermes_unavailable", "reason": exc.reason.value,
                "entryPoint": "analyze", "requestId": str(idempotency_key),
            }, sort_keys=True))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "ANALYSIS_PROVIDER_UNAVAILABLE"},
            ) from exc
        except LegalCoreProtocolError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "ANALYSIS_PROVIDER_UNAVAILABLE"},
            ) from exc
        except (HermesProtocolError, ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "ANALYSIS_PROVIDER_INVALID"},
            ) from exc

    answers = APIRouter(route_class=_AnswerMetadataRoute)
    answer_stage_timeout = HERMES_STAGE_TIMEOUT_SECONDS
    answer_wall_timeout = ANALYSIS_WALL_TIMEOUT_SECONDS

    @answers.post("/v1/intake-answer-preparations/{operation_id}/interpret",
                  response_model=AnswerProposal)
    async def interpret_answer(
        operation_id: UUID, request: UnderstandingInvocation,
        internal_key: InternalKeyHeader, telegram_user_id: TelegramUserIdHeader,
    ) -> AnswerProposal:
        require_internal_key(internal_key)
        if dependencies.answer_understanding is None:
            raise HTTPException(status_code=503, detail={"code": "MODEL_UNAVAILABLE"})
        try:
            async with asyncio.timeout(answer_wall_timeout):
                try:
                    supplied = await dependencies.legal_core.get_answer_context(
                        operation_id=operation_id, draft_id=request.draft_id,
                        telegram_user_id=telegram_user_id,
                        binding=UnderstandingContextRequest(
                            leaseToken=request.lease_token,
                            expectedRevision=request.expected_revision,
                            expectedInputSha256=request.expected_input_sha256,
                        ),
                    )
                    context = AnswerContext.model_validate(supplied.model_dump(by_alias=True))
                    answer_hash = hashlib.sha256(json.dumps(
                        {"answer": context.answer}, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"),
                    ).encode()).hexdigest()
                    if (
                        context.operation_id != operation_id or context.draft_id != request.draft_id
                        or context.draft_revision != request.expected_revision
                        or context.input_sha256 != request.expected_input_sha256
                        or context.answer_sha256 != answer_hash
                        or context.question_sha256 != context.question.question_sha256
                    ):
                        raise LegalCoreProtocolError("answer context binding mismatch")
                except (ValueError, TypeError, AttributeError):
                    raise LegalCoreProtocolError(
                        "answer context response contract mismatch",
                    ) from None
                async with asyncio.timeout(answer_stage_timeout):
                    interpretation = await dependencies.answer_understanding.interpret(
                        context.answer, context.question, dict(context.confirmed_context),
                        context.as_of_date,
                    )
                return AnswerProposal(
                    operationId=operation_id, draftId=context.draft_id,
                    draftRevision=context.draft_revision, inputSha256=context.input_sha256,
                    answerSha256=context.answer_sha256, questionSha256=context.question_sha256,
                    interpretation=interpretation,
                )
        except LegalCoreError as error:
            failure_status, code = error.status_code, "ANSWER_CONTEXT_REFUSED"
        except LegalCoreProtocolError:
            failure_status, code = 503, "ANSWER_CONTEXT_UNAVAILABLE"
        except TimeoutError:
            failure_status, code = 503, "MODEL_TIMEOUT"
        except HermesUnavailable:
            failure_status, code = 503, "MODEL_UNAVAILABLE"
        except (HermesProtocolError, ValueError, TypeError):
            failure_status, code = 502, "MODEL_PROTOCOL_INVALID"
        # No input-bearing context, transport request or model error is retained.
        raise HTTPException(status_code=failure_status, detail={"code": code})

    app.include_router(answers)
    app.include_router(create_understanding_router(
        legal_core=dependencies.legal_core, understanding=dependencies.understanding,
        require_internal_key=require_internal_key, stage_timeout=HERMES_STAGE_TIMEOUT_SECONDS,
        wall_timeout=ANALYSIS_WALL_TIMEOUT_SECONDS,
    ))
    return app


def create_runtime_app() -> FastAPI:
    settings = ServiceSettings.from_environment()
    return create_app(settings=settings, dependencies=build_dependencies(settings))
