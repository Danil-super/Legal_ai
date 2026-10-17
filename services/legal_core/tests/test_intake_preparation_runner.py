"""Real durable completion with a synthetic Agent; no model/provider network."""

import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest

from legal_core.analysis_job_worker import WorkerSettings
from legal_core.intake_preparation import cancel_preparation, read_preparation
from legal_core.intake_preparation_runner import IntakePreparationRunner
from legal_core.model_admission import ModelAdmission
from test_intake_preparation_lifecycle import _reserve, _run, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]
SETTINGS = WorkerSettings("http://synthetic-agent:8010", "synthetic-key-1234567890123456789012")


def _wire(request):
    body = json.loads(request.content)
    return {
        "operationId": request.url.path.split("/")[-2], "draftId": body["draftId"],
        "draftRevision": body["expectedRevision"], "inputSha256": body["expectedInputSha256"],
        "suggestions": {"clinicActions": ["INVITED_FOR_EXAMINATION"],
                        "sourceSpans": {"clinicActions": [{"start": 0, "end": 26}]}},
    }


@pytest.mark.parametrize("outcome", [
    "success", "busy", "cancel", "shutdown", "early_shutdown", "protocol", "timeout",
])
def test_runner_bounds_tasks_and_persists_completion_or_manual_fallback(draft_owner, outcome):
    actor, _, _, draft = draft_owner

    async def scenario(sessions):
        admission = ModelAdmission()
        if outcome == "busy":
            assert await admission.try_acquire() and await admission.try_acquire()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request)
            entered.set()
            if outcome == "timeout":
                raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_MARKER", request=request)
            if outcome in {"success", "cancel", "shutdown"}:
                await release.wait()
            wire = _wire(request)
            if outcome == "protocol":
                wire["draftId"] = str(uuid4())
            return httpx.Response(200, json=wire)

        lease = await _reserve(sessions, actor, draft)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            runner = IntakePreparationRunner(sessions, SETTINGS, admission, client=http)
            try:
                await runner.submit(telegram_user_id=actor, operation=lease)
                if outcome not in {"busy", "early_shutdown"}:
                    await asyncio.wait_for(entered.wait(), timeout=2)
                if outcome == "success":
                    pending = await read_preparation(
                        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
                    )
                    assert pending.state == "PENDING"
                if outcome == "cancel":
                    await cancel_preparation(
                        sessions, telegram_user_id=actor, operation_id=lease.operation_id,
                    )
                    runner.cancel(lease.operation_id)
                if outcome in {"shutdown", "early_shutdown"}:
                    await runner.close()
                release.set()
                finished = await asyncio.gather(*runner._tasks, return_exceptions=True)
                assert all(item is None or (
                    outcome == "cancel" and isinstance(item, asyncio.CancelledError)
                ) for item in finished)
                result = await read_preparation(
                    sessions, telegram_user_id=actor, operation_id=lease.operation_id,
                )
                assert result.state == ("PROPOSED" if outcome == "success"
                                        else "MANUAL_CONTINUATION")
                assert result.lease_token is None
                if outcome != "success":
                    assert result.suggestions is None
                assert len(calls) == (0 if outcome in {"busy", "early_shutdown"} else 1)
                assert all("source" not in json.loads(request.content) for request in calls)
            finally:
                release.set()
                await runner.close()
            if outcome == "busy":
                admission.release()
                admission.release()
            assert await admission.try_acquire() and await admission.try_acquire()
            assert not await admission.try_acquire()
            admission.release()
            admission.release()
    _run(scenario)


def test_application_lifespan_shares_one_budget_and_drains_before_engine_disposal(monkeypatch):
    from fastapi.testclient import TestClient
    from legal_core import main

    events, admissions = [], []

    class Engine:
        async def dispose(self):
            events.append("engine_disposed")

    class Runner:
        def __init__(self, sessions, settings, admission):
            admissions.append(admission)

        async def close(self):
            events.append("intake_drained")

    async def worker(sessions, settings, *, admission):
        admissions.append(admission)
        try:
            await asyncio.Event().wait()
        finally:
            events.append("analysis_drained")

    monkeypatch.setattr(main.WorkerSettings, "load", lambda: SETTINGS)
    monkeypatch.setattr(main, "IntakePreparationRunner", Runner)
    monkeypatch.setattr(main, "run_analysis_worker", worker)
    app = main.create_app(session_factory=object(), managed_engine=Engine(),
                          enable_draft_retention=False)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert app.state.intake_preparation_runner is not None
    assert len(admissions) == 2 and admissions[0] is admissions[1]
    assert events == ["intake_drained", "analysis_drained", "engine_disposed"]
    assert app.state.intake_preparation_runner is None
