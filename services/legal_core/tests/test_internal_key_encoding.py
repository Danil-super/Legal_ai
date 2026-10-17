"""Malformed service credentials must be refused before any protected operation."""

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from legal_core.analysis_api import _require_agent_internal_key
from legal_core.case_api import ApiError

KEY = "synthetic-agent-key-123456789012345678901234"


@pytest.mark.parametrize("header", [b"\xff" * 32, b"\xc3\xa9" * 32, b"x" * 32, None])
def test_nonmatching_service_key_returns_bounded_forbidden_without_crashing(header, monkeypatch):
    monkeypatch.setenv("AGENT_INTERNAL_KEY", KEY)
    app, operations = FastAPI(), []

    @app.exception_handler(ApiError)
    async def denied(request: Request, error: ApiError):
        return JSONResponse(status_code=error.status_code, content={"code": error.code})

    @app.get("/protected", dependencies=[Depends(_require_agent_internal_key)])
    async def protected():
        operations.append(True)
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/protected", headers={"X-Agent-Internal-Key": header}
                              if header is not None else {})
        assert response.status_code == 403 and response.json() == {
            "code": "INTERNAL_ACCESS_REQUIRED"}
        assert not operations
        accepted = client.get("/protected", headers={"X-Agent-Internal-Key": KEY})
        assert accepted.status_code == 200 and accepted.json() == {"ok": True}
        assert operations == [True]
