"""Standalone read-only preview API; never imported by legal_core.main.

Enabled mode permits fixed catalogue/scenario reads only. No SQL, object storage,
provider, actor provisioning, submitted facts, clinical routing or case writes.
"""

import os
import re
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from legal_core.personal.catalog import CATALOG, PREVIEW_NOTICE, get_topic, render_demo
from legal_core.personal.settings import PreviewMode, PreviewSettings


def create_app(settings: PreviewSettings | None = None) -> FastAPI:
    config = settings if settings is not None else PreviewSettings.from_mapping(os.environ)
    app = FastAPI(
        title="Personal synthetic preview", openapi_url=None, docs_url=None, redoc_url=None,
    )

    @app.middleware("http")
    async def private_response(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, error: RequestValidationError) -> JSONResponse:
        del request, error
        return JSONResponse(status_code=422, content={"error": "invalid request"})

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "service": "personal-preview", "mode": config.mode.value}

    if config.mode is PreviewMode.OFF:
        return app

    def authorized(request: Request) -> bool:
        # The service token authenticates the preview gateway, not a self-asserted user ID.
        # Both it and the allowlist are required; this is NOT the future public auth API.
        auth = request.headers.getlist("authorization")
        actors = request.headers.getlist("x-personal-preview-tester")
        if len(auth) != 1 or len(actors) != 1:
            return False
        if len(auth[0]) > 160 or not re.fullmatch(r"[A-Za-z0-9_ -]+", auth[0]):
            return False
        if not secrets.compare_digest(auth[0], f"Bearer {config.api_key}"):
            return False
        raw_actor = actors[0]
        return bool(
            re.fullmatch(r"[1-9][0-9]{0,15}", raw_actor)
            and config.allows_tester(int(raw_actor))
        )

    def denied() -> JSONResponse:
        # Do not reveal the tester list or whether any personal case exists.
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    @app.get("/v1/personal-preview/catalog", response_model=None)
    async def catalog(request: Request) -> Any:
        if not authorized(request):
            return denied()
        return {
            "schema_version": "personal-preview.v1", "mode": "synthetic",
            "live_analysis": False, "notice": PREVIEW_NOTICE,
            "topics": [
                {"id": card.topic.value, "audience": card.audience.value, "title": card.title}
                for card in CATALOG
            ],
        }

    @app.get("/v1/personal-preview/scenarios/{scenario_id}", response_model=None)
    async def scenario(scenario_id: str, request: Request) -> Any:
        if not authorized(request):
            return denied()
        card = get_topic(scenario_id)
        if card is None:
            return denied()
        return {
            "schema_version": "personal-preview.v1", "synthetic": True,
            "analysis_status": "NOT_AVAILABLE", "topic": card.topic.value,
            "text": render_demo(card),
        }

    return app


# Factory only: no module-level startup with a clinical environment or shared app.
