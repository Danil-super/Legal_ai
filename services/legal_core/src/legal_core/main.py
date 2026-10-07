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
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.middleware.base import RequestResponseEndpoint

from legal_core import __version__
from legal_core.analysis_api import create_analysis_router
from legal_core.analysis_diagnostics import create_analysis_diagnostics_router
from legal_core.analysis_job_worker import WorkerSettings, run_analysis_worker
from legal_core.analysis_jobs import create_analysis_jobs_router
from legal_core.case_api import ApiError, create_case_router
from legal_core.case_material_retention import purge_expired_case_materials
from legal_core.case_materials_api import create_case_materials_router
from legal_core.case_retention import purge_expired_case_content
from legal_core.clinic_document_library import create_clinic_document_library_router
from legal_core.clinic_document_store import (
    RawCaseMaterialStore,
    RawClinicDocumentStore,
    RawReferenceEvaluationStore,
    minio_store_from_environment,
)
from legal_core.clinic_documents_api import create_clinic_documents_router
from legal_core.database import create_engine, create_session_factory
from legal_core.draft_retention import purge_expired_intake_drafts
from legal_core.escalation_notifications import create_escalation_notifications_router
from legal_core.legal_api import create_legal_router
from legal_core.reference_evaluation_api import create_reference_evaluation_router
from legal_core.reference_evaluation_retention import purge_expired_reference_evaluations

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


async def _probe_postgres(session_factory: async_sessionmaker[AsyncSession]) -> bool:
    """Verify the application role can execute a minimal query, not merely open TCP."""

    try:
        async with asyncio.timeout(2):
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        return False
    return True


async def _probe_redis() -> bool:
    """Require an actual Redis PONG before reporting ready."""

    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(2):
            reader, writer = await asyncio.open_connection(
                os.getenv("REDIS_HOST", "redis"),
                int(os.getenv("REDIS_PORT", "6379")),
            )
            writer.write(b"*1\r\n$4\r\nPING\r\n")
            await writer.drain()
            return await reader.readuntil(b"\r\n") == b"+PONG\r\n"
    except (
        OSError,
        TimeoutError,
        ValueError,
        asyncio.LimitOverrunError,
        asyncio.IncompleteReadError,
    ):
        return False
    finally:
        if writer is not None:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()


async def _probe_object_storage() -> bool:
    """Check a signed bucket request so credentials, not only MinIO's port, are valid."""

    try:
        async with asyncio.timeout(2):
            return await minio_store_from_environment().probe()
    except (RuntimeError, ValueError, TimeoutError):
        return False


async def probe_dependencies(session_factory: async_sessionmaker[AsyncSession]) -> ReadinessChecks:
    """Check executable runtime capabilities without exposing dependency data."""

    postgres, redis, object_storage = await asyncio.gather(
        _probe_postgres(session_factory),
        _probe_redis(),
        _probe_object_storage(),
    )
    return {
        "postgres": postgres,
        "redis": redis,
        "object_storage": object_storage,
    }


async def _retention_purge_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Run bounded retention at startup and hourly without reading retained contents."""

    while True:
        try:
            await purge_expired_case_materials(session_factory, minio_store_from_environment())
        except (RuntimeError, ValueError, SQLAlchemyError):
            logging.getLogger(__name__).exception("Case material retention purge failed")
        try:
            await purge_expired_reference_evaluations(
                session_factory, minio_store_from_environment()
            )
        except (RuntimeError, ValueError, SQLAlchemyError):
            logging.getLogger(__name__).exception("Reference evaluation retention purge failed")
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
    readiness_probe: ReadinessProbe | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    managed_engine: AsyncEngine | None = None,
    enable_draft_retention: bool = True,
    clinic_document_store: RawClinicDocumentStore | None = None,
    case_material_store: RawCaseMaterialStore | None = None,
    reference_evaluation_store: RawReferenceEvaluationStore | None = None,
    enable_analysis_worker: bool = True,
) -> FastAPI:
    engine = managed_engine or create_engine()
    sessions = session_factory or create_session_factory(engine)

    async def runtime_readiness_probe() -> ReadinessChecks:
        return await probe_dependencies(sessions)

    active_readiness_probe = readiness_probe or runtime_readiness_probe

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        del application
        retention_task = (
            asyncio.create_task(_retention_purge_loop(sessions)) if enable_draft_retention else None
        )
        worker_settings = WorkerSettings.load() if enable_analysis_worker else None
        worker_task = (
            asyncio.create_task(run_analysis_worker(sessions, worker_settings))
            if worker_settings is not None else None
        )
        try:
            yield
        finally:
            if worker_task is not None:
                worker_task.cancel()
                with suppress(asyncio.CancelledError):
                    await worker_task
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
        checks = await active_readiness_probe()
        is_ready = bool(checks) and all(checks.values())
        if not is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="ready" if is_ready else "not_ready",
            checks=checks,
        )

    app.include_router(create_case_router(sessions))
    app.include_router(create_case_materials_router(sessions, raw_store=case_material_store))
    app.include_router(
        create_reference_evaluation_router(sessions, raw_store=reference_evaluation_store)
    )
    app.include_router(create_legal_router(sessions))
    app.include_router(
        create_clinic_documents_router(
            sessions,
            raw_store=clinic_document_store,
        )
    )
    app.include_router(create_clinic_document_library_router(sessions))
    app.include_router(create_analysis_router(sessions))
    app.include_router(create_analysis_jobs_router(sessions))
    app.include_router(create_escalation_notifications_router(sessions))
    app.include_router(create_analysis_diagnostics_router(sessions))

    return app


app = create_app()
