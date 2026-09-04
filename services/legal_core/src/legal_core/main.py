"""FastAPI entrypoint for the Legal Core service."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.middleware.base import RequestResponseEndpoint

from legal_core import __version__
from legal_core.analysis_api import create_analysis_router
from legal_core.case_api import ApiError, create_case_router
from legal_core.case_retention import purge_expired_case_content
from legal_core.clinic_document_library import create_clinic_document_library_router
from legal_core.clinic_document_store import RawClinicDocumentStore
from legal_core.clinic_documents_api import create_clinic_documents_router
from legal_core.database import create_engine, create_session_factory
from legal_core.draft_retention import purge_expired_intake_drafts
from legal_core.legal_api import create_legal_router

SERVICE_NAME = "legal-core"
DRAFT_PURGE_INTERVAL_SECONDS = 60 * 60
ReadinessChecks = dict[str, bool]
ReadinessProbe = Callable[[], Awaitable[ReadinessChecks]]


class LiveResponse(BaseModel):
    """Stable liveness contract; it intentionally performs no external I/O."""

    status: Literal["ok"]
    service: str
    version: str


class ReadinessResponse(BaseModel):
    """Dependency readiness contract used by orchestrators and operators."""

    status: Literal["ready", "not_ready"]
    checks: ReadinessChecks


async def _tcp_reachable(host: str, port: int, timeout_seconds: float = 1.0) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout_seconds
        )
    except (OSError, TimeoutError):
        return False

    writer.close()
    await writer.wait_closed()
    return True


async def probe_dependencies() -> ReadinessChecks:
    """Check local dependency reachability without reading or exposing data."""

    endpoints = {
        "postgres": (
            os.getenv("POSTGRES_HOST", "postgres"),
            int(os.getenv("POSTGRES_PORT", "5432")),
        ),
        "redis": (os.getenv("REDIS_HOST", "redis"), int(os.getenv("REDIS_PORT", "6379"))),
        "object_storage": (
            os.getenv("MINIO_HOST", "minio"),
            int(os.getenv("MINIO_PORT", "9000")),
        ),
    }
    results = await asyncio.gather(
        *(_tcp_reachable(host, port) for host, port in endpoints.values())
    )
    return dict(zip(endpoints, results, strict=True))


async def _retention_purge_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Run bounded retention at startup and hourly without reading retained contents."""

    while True:
        try:
            await purge_expired_intake_drafts(session_factory)
        except Exception:  # pragma: no cover - operator-visible process log is the recovery path.
            logging.getLogger(__name__).exception("Telegram draft retention purge failed")
        try:
            await purge_expired_case_content(session_factory)
        except Exception:  # pragma: no cover - operator-visible process log is the recovery path.
            logging.getLogger(__name__).exception("Case content retention purge failed")
        await asyncio.sleep(DRAFT_PURGE_INTERVAL_SECONDS)


def create_app(
    readiness_probe: ReadinessProbe = probe_dependencies,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    managed_engine: AsyncEngine | None = None,
    enable_draft_retention: bool = True,
    clinic_document_store: RawClinicDocumentStore | None = None,
) -> FastAPI:
    engine = managed_engine or create_engine()
    sessions = session_factory or create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        del application
        retention_task = (
            asyncio.create_task(_retention_purge_loop(sessions)) if enable_draft_retention else None
        )
        try:
            yield
        finally:
            if retention_task is not None:
                retention_task.cancel()
                with suppress(asyncio.CancelledError):
                    await retention_task
            await engine.dispose()

    app = FastAPI(
        title="Dental Legal AI — Legal Core",
        version=__version__,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        description=(
            "Versioned evidence and case services. Legal conclusions require retrieved evidence."
        ),
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def correlation_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.correlation_id = str(uuid4())
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                    "correlationId": request.state.correlation_id,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        details = [{"location": list(item["loc"]), "type": item["type"]} for item in error.errors()]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": details,
                    "correlationId": request.state.correlation_id,
                }
            },
        )

    @app.get("/health/live", response_model=LiveResponse, tags=["health"])
    async def live() -> LiveResponse:
        return LiveResponse(status="ok", service=SERVICE_NAME, version=__version__)

    @app.get(
        "/health/ready",
        response_model=ReadinessResponse,
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
        tags=["health"],
    )
    async def ready(response: Response) -> ReadinessResponse:
        checks = await readiness_probe()
        is_ready = bool(checks) and all(checks.values())
        if not is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="ready" if is_ready else "not_ready",
            checks=checks,
        )

    app.include_router(create_case_router(sessions))
    app.include_router(create_legal_router(sessions))
    app.include_router(
        create_clinic_documents_router(
            sessions,
            raw_store=clinic_document_store,
        )
    )
    app.include_router(create_clinic_document_library_router(sessions))
    app.include_router(create_analysis_router(sessions))

    return app


app = create_app()
