"""Internal FastAPI service coordinating Legal Core and two isolated Hermes profiles."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, status

from agent_orchestrator.hermes_client import (
    HermesClient,
    HermesEndpoint,
    HermesProtocolError,
    HermesUnavailable,
)
from agent_orchestrator.legal_core_client import (
    LegalCoreClient,
    LegalCoreEndpoint,
    LegalCoreError,
    LegalCoreProtocolError,
)
from agent_orchestrator.projection import build_projection_from_context
from agent_orchestrator.reasoning import LegalReasoningOrchestrator
from legal_core.analysis_contracts import AnalysisSubmissionResponse

InternalKeyHeader = Annotated[
    str,
    Header(alias="X-Agent-Internal-Key", min_length=16, max_length=512),
]
TelegramUserIdHeader = Annotated[int, Header(alias="X-Telegram-User-Id", gt=0)]
IdempotencyKeyHeader = Annotated[UUID, Header(alias="Idempotency-Key")]


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


def build_dependencies(settings: ServiceSettings) -> ServiceDependencies:
    legal_core = LegalCoreClient(LegalCoreEndpoint(base_url=settings.legal_core_url))
    researcher = HermesClient(
        HermesEndpoint(
            base_url=settings.hermes_researcher_url,
            api_key=settings.hermes_researcher_key,
            model=settings.hermes_researcher_model,
        )
    )
    reviewer = HermesClient(
        HermesEndpoint(
            base_url=settings.hermes_reviewer_url,
            api_key=settings.hermes_reviewer_key,
            model=settings.hermes_reviewer_model,
        )
    )
    return ServiceDependencies(
        legal_core=legal_core,
        reasoning=LegalReasoningOrchestrator(researcher=researcher, reviewer=reviewer),
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
        response_model=AnalysisSubmissionResponse,
    )
    async def analyze_case(
        case_id: UUID,
        x_agent_internal_key: InternalKeyHeader,
        telegram_user_id: TelegramUserIdHeader,
        idempotency_key: IdempotencyKeyHeader,
    ) -> AnalysisSubmissionResponse:
        require_internal_key(x_agent_internal_key)
        try:
            context = await dependencies.legal_core.get_analysis_context(
                case_id=case_id,
                telegram_user_id=telegram_user_id,
            )
            projection = build_projection_from_context(context)
            reasoning = await dependencies.reasoning.reason(projection)
            return await dependencies.legal_core.submit_reasoning(
                context=context,
                reasoning=reasoning,
                telegram_user_id=telegram_user_id,
                idempotency_key=idempotency_key,
            )
        except LegalCoreError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except (HermesUnavailable, LegalCoreProtocolError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "ANALYSIS_PROVIDER_UNAVAILABLE"},
            ) from exc
        except (HermesProtocolError, ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "ANALYSIS_PROVIDER_INVALID"},
            ) from exc

    return app


def create_runtime_app() -> FastAPI:
    settings = ServiceSettings.from_environment()
    return create_app(settings=settings, dependencies=build_dependencies(settings))
