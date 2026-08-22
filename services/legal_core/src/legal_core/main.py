"""FastAPI entrypoint for the Legal Core service."""

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import FastAPI, Response, status
from pydantic import BaseModel

from legal_core import __version__

SERVICE_NAME = "legal-core"
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


def create_app(readiness_probe: ReadinessProbe = probe_dependencies) -> FastAPI:
    app = FastAPI(
        title="Dental Legal AI — Legal Core",
        version=__version__,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        description=(
            "Versioned evidence and case services. "
            "Legal conclusions require retrieved evidence."
        ),
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

    return app


app = create_app()
