"""An operator stop must fence automation without erasing human work."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from legal_core import analysis_api, analysis_job_worker, analysis_diagnostics
from legal_core.case_api import ApiError
from telegram_gateway.analysis_runtime import analysis_error_message


def test_stop_requires_explicit_zero_to_resume_and_does_not_change_default():
    from legal_core.analysis_safe_stop import analysis_safe_stop_active

    assert analysis_safe_stop_active({}) is False
    assert analysis_safe_stop_active({"LEGAL_ANALYSIS_SAFE_STOP": "0"}) is False
    assert analysis_safe_stop_active({"LEGAL_ANALYSIS_SAFE_STOP": " 0"}) is True
    assert analysis_safe_stop_active({"LEGAL_ANALYSIS_SAFE_STOP": "false"}) is True


@pytest.mark.parametrize("value", ["1", "true", "", "bad-setting"])
def test_stop_or_invalid_setting_leaves_analysis_backlog_unclaimed(monkeypatch, value):
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", value)

    class NoNetwork:
        async def get(self, *_args, **_kwargs):
            pytest.fail("safe-stop must not reach the analysis service")

    result = asyncio.run(analysis_job_worker.worker_once(
        None, NoNetwork(), analysis_job_worker.WorkerSettings("http://orchestrator", "x" * 32),
    ))
    assert result is False


def test_stop_fences_context_before_fact_or_corpus_read(monkeypatch):
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    case = SimpleNamespace(
        id=uuid4(),
        closed_at=datetime.now(UTC),
        status="ANALYSIS_BLOCKED",
        intake_schema_version="dental-case-intake.v2",
        retention_due_at=datetime.now(UTC) + timedelta(days=1),
    )
    monkeypatch.setattr(analysis_api, "_tenant_case", AsyncMock(return_value=case))
    facts = AsyncMock(side_effect=AssertionError("no analysis data reads while stopped"))
    monkeypatch.setattr(analysis_api, "_current_fact_rows", facts)
    with pytest.raises(ApiError) as raised:
        asyncio.run(analysis_api._load_analysis_state(None, object(), uuid4()))
    assert raised.value.status_code == 503
    assert raised.value.code == "ANALYSIS_SAFE_STOP"
    facts.assert_not_awaited()


def test_diagnostics_exposes_safe_stop_before_runtime_probe(monkeypatch):
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    monkeypatch.setattr(analysis_diagnostics, "WorkerSettings", SimpleNamespace(
        load=lambda: pytest.fail("safe-stop must be visible without a provider probe"),
    ))
    assert asyncio.run(analysis_diagnostics.inspect_analysis_runtime()) == "SAFE_STOP"


def test_safe_stop_message_is_actionable_without_configuration_details():
    message = analysis_error_message("ANALYSIS_SAFE_STOP")
    assert "приостановлен" in message
    assert "юрист" in message
    assert "повтор" in message
    assert "SAFE_STOP" not in message
