"""Read-only diagnosis must never bypass clinic authorization or call an LLM."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from legal_core import analysis_diagnostics as diagnostics
from legal_core.case_api import ApiError
from telegram_gateway.analysis_diagnostics_runtime import render_analysis_diagnostics


@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "CLINIC_LAWYER"])
def test_only_owner_can_read_prerequisites(monkeypatch, role):
    monkeypatch.setattr(diagnostics, "resolve_actor", AsyncMock(
        return_value=SimpleNamespace(role=role),
    ))
    policy = Mock(side_effect=AssertionError("no reads before authorization"))
    probe = AsyncMock(side_effect=AssertionError("no network before authorization"))
    monkeypatch.setattr(diagnostics, "ApprovedRiskPolicyRepository", policy)
    monkeypatch.setattr(diagnostics, "inspect_analysis_runtime", probe)
    with pytest.raises(ApiError) as raised:
        asyncio.run(diagnostics.collect_analysis_diagnostics(Mock(), 123))
    assert raised.value.status_code == 403
    policy.assert_not_called()
    probe.assert_not_called()


def test_inactive_user_stops_before_any_diagnostics(monkeypatch):
    monkeypatch.setattr(diagnostics, "resolve_actor", AsyncMock(side_effect=ApiError(
        status_code=403, code="SUBSCRIPTION_INACTIVE", message="inactive",
    )))
    probe = AsyncMock()
    monkeypatch.setattr(diagnostics, "inspect_analysis_runtime", probe)
    with pytest.raises(ApiError):
        asyncio.run(diagnostics.collect_analysis_diagnostics(Mock(), 123))
    probe.assert_not_called()


@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("has_documents", [True, False])
def test_metadata_uses_only_approved_today_and_tenant_reports(monkeypatch, approved, has_documents):
    clinic_id = UUID(int=44)
    monkeypatch.setattr(diagnostics, "resolve_actor", AsyncMock(return_value=SimpleNamespace(
        role="CLINIC_OWNER", clinic_id=clinic_id,
    )))
    policy = SimpleNamespace(get=AsyncMock(
        return_value=object(), side_effect=None if approved else LookupError("not ready"),
    ))
    corpus = SimpleNamespace(list_documents=AsyncMock(
        return_value=[object()] if has_documents else [],
    ))
    monkeypatch.setattr(diagnostics, "ApprovedRiskPolicyRepository", lambda session: policy)
    monkeypatch.setattr(diagnostics, "ApprovedLegalCorpusRepository", lambda session: corpus)
    monkeypatch.setattr(diagnostics, "inspect_analysis_runtime", AsyncMock(return_value="DISABLED"))
    monkeypatch.setenv("DENTAL_RELEASE_SHA", "a" * 40)
    last = datetime(2026, 9, 19, tzinfo=UTC)
    session = SimpleNamespace(scalar=AsyncMock(return_value=last))
    result = asyncio.run(diagnostics.collect_analysis_diagnostics(session, 123))
    assert result.risk_policy == ("APPROVED" if approved else "NOT_READY")
    assert result.legal_corpus_today == ("PRESENT" if has_documents else "EMPTY")
    assert result.model_execution == result.case_coverage == "NOT_TESTED"
    assert result.last_successful_report_at == last
    assert result.source_revision == "a" * 40
    corpus.list_documents.assert_awaited_once_with(as_of_date=result.checked_at.date(), limit=1)
    query = session.scalar.call_args.args[0]
    compiled = query.compile()
    assert "case_reports.clinic_id =" in str(compiled)
    assert clinic_id in compiled.params.values()
    assert ["REPORT_READY", "ESCALATION_REQUIRED"] in compiled.params.values()


@pytest.mark.parametrize("value", ["", "secret-token", "A" * 40, "a" * 41, "a" * 39])
def test_revision_never_echoes_arbitrary_environment(monkeypatch, value):
    monkeypatch.setenv("DENTAL_RELEASE_SHA", value)
    assert diagnostics._source_revision() is None


def test_disabled_and_bad_configuration_never_probe(monkeypatch):
    monkeypatch.setattr(diagnostics.httpx, "AsyncClient", Mock(
        side_effect=AssertionError("network"),
    ))
    monkeypatch.delenv("AGENT_ORCHESTRATOR_URL", raising=False)
    assert asyncio.run(diagnostics.inspect_analysis_runtime()) == "DISABLED"
    monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://agent-orchestrator:8010")
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "short")
    assert asyncio.run(diagnostics.inspect_analysis_runtime()) == "CONFIG_INVALID"


@pytest.mark.parametrize("http_status,expected", [(200, "REACHABLE"), (302, "UNREACHABLE"),
                                                  (503, "UNREACHABLE")])
def test_liveness_is_not_model_readiness(monkeypatch, http_status, expected):
    monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://agent-orchestrator:8010")
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "private-key-that-must-not-be-sent-123456789")
    real_client = httpx.AsyncClient

    def handle(request):
        assert request.method == "GET"
        assert request.url.path == "/health/live"
        assert "authorization" not in request.headers
        assert "x-agent-internal-key" not in request.headers
        return httpx.Response(http_status)

    monkeypatch.setattr(diagnostics.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    assert asyncio.run(diagnostics.inspect_analysis_runtime()) == expected


def test_network_failure_is_not_a_success(monkeypatch):
    monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://agent-orchestrator:8010")
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "x" * 32)
    real_client = httpx.AsyncClient

    def handle(request):
        raise httpx.ConnectError("private internal address", request=request)

    monkeypatch.setattr(diagnostics.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    assert asyncio.run(diagnostics.inspect_analysis_runtime()) == "UNREACHABLE"


def test_route_requires_identity_and_uses_owner_gate(monkeypatch):
    @asynccontextmanager
    async def sessions():
        yield Mock()

    actor = AsyncMock(side_effect=ApiError(
        status_code=403, code="ACTOR_NOT_AUTHORIZED", message="no",
    ))
    monkeypatch.setattr(diagnostics, "resolve_actor", actor)
    app = FastAPI()
    app.include_router(diagnostics.create_analysis_diagnostics_router(sessions))

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code})

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test",
        ) as client:
            assert (await client.get("/v1/analysis-diagnostics")).status_code == 422
            actor.assert_not_called()
            response = await client.get(
                "/v1/analysis-diagnostics", headers={"X-Telegram-User-Id": "123"},
            )
            assert response.status_code == 403
            actor.assert_awaited_once()

    asyncio.run(check())


@pytest.mark.parametrize(
    "runtime", ["DISABLED", "CONFIG_INVALID", "UNREACHABLE", "REACHABLE", "SAFE_STOP"],
)
def test_display_shows_limits_and_never_config_or_case_text(runtime):
    payload = {
        "runtime": runtime, "riskPolicy": "NOT_READY", "legalCorpusToday": "EMPTY",
        "checkedAt": "2026-09-20T13:00:00+00:00", "sourceRevision": "not-a-sha-secret",
        "lastSuccessfulReportAt": None, "apiKey": "test-secret-never-render",
        "caseText": "DO_NOT_PRINT", "sourceUrl": "http://private.internal",
    }
    text = render_analysis_diagnostics(payload)
    assert "не вызывает модели" in text
    assert "не подтверждает" in text
    assert "NOT_READY" not in text
    assert all(value not in text for value in (
        "not-a-sha-secret", "test-secret-never-render", "DO_NOT_PRINT", "private.internal",
    ))


@pytest.mark.parametrize("field,value", [("runtime", "OTHER"), ("riskPolicy", []),
                                          ("checkedAt", "broken"),
                                          ("lastSuccessfulReportAt", {})])
def test_display_rejects_invalid_metadata(field, value):
    payload = {
        "runtime": "DISABLED", "riskPolicy": "NOT_READY", "legalCorpusToday": "EMPTY",
        "checkedAt": "2026-09-20T13:00:00+00:00", "lastSuccessfulReportAt": None,
    }
    payload[field] = value
    with pytest.raises(ValueError):
        render_analysis_diagnostics(payload)
