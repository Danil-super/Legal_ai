"""Independent lock-reload regression; no PostgreSQL or network needed."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from datetime import UTC, datetime

import pytest

from legal_core import analysis_api, analysis_job_worker, analysis_jobs, case_preparation
from legal_core.case_api import ActorContext, ApiError
from legal_core.models import Case


def test_submission_rechecks_lawyer_creator_after_locked_reload(monkeypatch):
    actor = ActorContext(user_id=uuid4(), membership_id=uuid4(), clinic_id=uuid4(),
                         role="CLINIC_LAWYER")
    case_id = uuid4()
    own = Case(id=case_id, clinic_id=actor.clinic_id,
               created_by_membership_id=actor.membership_id, status="ANALYSIS_BLOCKED")
    reassigned = Case(id=case_id, clinic_id=actor.clinic_id,
                      created_by_membership_id=uuid4(), status="ESCALATION_REQUIRED")
    monkeypatch.setattr(analysis_api, "resolve_actor", AsyncMock(return_value=actor))
    # Both reads are tenant-visible: the second represents the freshly locked
    # escalated row, which lawyers may read but cannot mutate for another creator.
    monkeypatch.setattr(analysis_api, "_tenant_case", AsyncMock(side_effect=[own, reassigned]))
    monkeypatch.setattr(analysis_api, "recheck_submission_access", AsyncMock())
    router = analysis_api.create_analysis_router(SimpleNamespace())
    endpoint = next(route.endpoint for route in router.routes
                    if route.path.endswith("/preparation-context"))
    closure = dict(zip(endpoint.__code__.co_freevars,
                       [cell.cell_contents for cell in endpoint.__closure__], strict=True))

    async def run():
        with pytest.raises(ApiError) as error:
            await closure["submission_case"](
                case_id, 99_000_000_001, uuid4(), SimpleNamespace(), None, None, 1,
            )
        assert error.value.status_code == 404
        assert error.value.code == "CASE_NOT_FOUND"
    asyncio.run(run())


def test_enqueue_rechecks_lawyer_creator_after_locked_reload(monkeypatch):
    actor = ActorContext(user_id=uuid4(), membership_id=uuid4(), clinic_id=uuid4(),
                         role="CLINIC_LAWYER")
    case_id = uuid4()
    own = Case(id=case_id, clinic_id=actor.clinic_id,
               created_by_membership_id=actor.membership_id, status="ANALYSIS_BLOCKED",
               closed_at=datetime.now(UTC))
    reassigned = Case(id=case_id, clinic_id=actor.clinic_id,
                      created_by_membership_id=uuid4(), status="ESCALATION_REQUIRED",
                      closed_at=datetime.now(UTC))
    monkeypatch.setattr(analysis_jobs, "resolve_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(analysis_jobs, "_tenant_case", AsyncMock(side_effect=[own, reassigned]))
    monkeypatch.setattr(analysis_jobs, "require_analysis_running", lambda: None)
    monkeypatch.setattr(analysis_job_worker.WorkerSettings, "load", lambda: object())
    monkeypatch.setattr(case_preparation, "recheck_submission_access", AsyncMock())
    # The first scalar finds a v2 workflow. Reaching job discovery after the
    # locked read would cross the fresh-intent authorization boundary.
    session = SimpleNamespace(execute=AsyncMock(), scalar=AsyncMock(side_effect=[
        uuid4(), AssertionError("foreign case reached fresh job discovery"),
    ]))
    router = analysis_jobs.create_analysis_jobs_router(SimpleNamespace())
    endpoint = next(route.endpoint for route in router.routes
                    if route.path.endswith("/{case_id}/analysis-jobs"))

    async def run():
        with pytest.raises(ApiError) as error:
            await endpoint(case_id, analysis_jobs.AnalysisJobRequest(messageId=1),
                           99_000_000_001, session)
        assert error.value.status_code == 404
        assert error.value.code == "CASE_NOT_FOUND"
    asyncio.run(run())
