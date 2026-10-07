"""Bounded background execution; PostgreSQL holds acknowledged work across restarts."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.analysis_jobs import AnalysisJob, ClaimedJob, claim_job, job_result
from legal_core.analysis_safe_stop import analysis_safe_stop_active, require_analysis_running
from legal_core.case_api import ApiError, _tenant_case, resolve_actor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerSettings:
    url: str
    internal_key: str

    @classmethod
    def load(cls, source: Mapping[str, str] | None = None) -> WorkerSettings | None:
        source = os.environ if source is None else source
        url = source.get("AGENT_ORCHESTRATOR_URL", "").strip().rstrip("/")
        if not url:
            return None
        key = source.get("AGENT_INTERNAL_KEY", "")
        parsed = urlsplit(url)
        if (
            len(key) < 32
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("analysis worker configuration is invalid")
        return cls(url, key)


async def execute_job(
    sessions: async_sessionmaker[AsyncSession],
    job: ClaimedJob,
    client: httpx.AsyncClient,
    settings: WorkerSettings,
) -> None:
    async with sessions() as session:
        actor = await resolve_actor(session, job.telegram_user_id)
        if actor.clinic_id != job.clinic_id or actor.membership_id != job.actor_membership_id:
            raise ApiError(status_code=403, code="ACTOR_NOT_AUTHORIZED", message="Actor changed")
        case = await _tenant_case(session, actor, job.case_id)
        if case.retention_due_at is not None and case.retention_due_at <= datetime.now(UTC):
            raise ApiError(status_code=410, code="CASE_CONTENT_EXPIRED", message="Case expired")
        stored = await session.get(AnalysisJob, job.id)
        if stored is not None and await job_result(session, stored) is not None:
            return  # Submission committed before a crash/HTTP timeout; never run the models again.
    require_analysis_running()
    response = await asyncio.wait_for(
        client.post(
            f"{settings.url}/v1/cases/{job.case_id}/analyze",
            headers={
                "X-Agent-Internal-Key": settings.internal_key,
                "X-Telegram-User-Id": str(job.telegram_user_id),
                "Idempotency-Key": str(job.id),
                "X-Analysis-Job-Id": str(job.id),
                "X-Analysis-Job-Token": str(job.lease_token),
            },
        ),
        timeout=125,
    )
    if response.is_error:
        code = "ANALYSIS_SERVICE_UNAVAILABLE"
        try:
            detail = response.json().get("detail", {})
            candidate = detail.get("code") if isinstance(detail, dict) else None
            if isinstance(candidate, str) and re.fullmatch(r"[A-Z_]{1,80}", candidate):
                code = candidate
        except (ValueError, AttributeError):
            pass
        raise ApiError(status_code=response.status_code, code=code, message="Analysis failed")


async def finish_job(
    sessions: async_sessionmaker[AsyncSession],
    claimed: ClaimedJob,
    error: str | None,
) -> None:
    async with sessions() as session:
        # Clinic comes only from the database claim, never from a request payload.
        await session.execute(
            text("SELECT set_config('app.current_clinic_id',:clinic,true)"),
            {"clinic": str(claimed.clinic_id)},
        )
        job = await session.scalar(
            select(AnalysisJob)
            .where(
                AnalysisJob.id == claimed.id,
                AnalysisJob.clinic_id == claimed.clinic_id,
            )
            .with_for_update()
        )
        if job is None or job.state != "RUNNING" or job.lease_token != claimed.lease_token:
            return
        result = await job_result(session, job)
        job.state = "SUCCEEDED" if result is not None else "FAILED"
        job.error_code = None if result is not None else error or "ANALYSIS_RESULT_MISSING"
        job.lease_token = None
        job.lease_until = None
        job.updated_at = datetime.now(UTC)
        await session.commit()


async def process_job(
    sessions: async_sessionmaker[AsyncSession],
    job: ClaimedJob,
    client: httpx.AsyncClient,
    settings: WorkerSettings,
) -> None:
    started = time.monotonic()
    error = None
    try:
        await execute_job(sessions, job, client, settings)
    except ApiError as exc:
        error = exc.code
    except (httpx.HTTPError, TimeoutError):
        error = "ANALYSIS_SERVICE_UNAVAILABLE"
    except Exception:
        # No exception values/tracebacks: downstream errors can carry case text or credentials.
        error = "ANALYSIS_INTERNAL_ERROR"
    await finish_job(sessions, job, error)
    logger.info(
        "analysis_job_finished job=%s duration_ms=%d outcome=%s",
        job.id,
        int((time.monotonic() - started) * 1000),
        error or "SUBMITTED",
    )


async def worker_once(
    sessions: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    settings: WorkerSettings,
) -> bool:
    if analysis_safe_stop_active():
        return False
    # Compose starts Core before orchestrator. Leave the durable backlog untouched until
    # the configured consumer is reachable; startup order is not an analysis failure.
    try:
        ready = await client.get(f"{settings.url}/health/live", timeout=2)
    except httpx.HTTPError:
        return False
    if ready.status_code != 200:
        return False
    job = await claim_job(sessions)
    if job is None:
        return False
    await process_job(sessions, job, client, settings)
    return True


async def run_analysis_worker(
    sessions: async_sessionmaker[AsyncSession],
    settings: WorkerSettings,
) -> None:
    async with httpx.AsyncClient(
        timeout=120, trust_env=False, follow_redirects=False, limits=httpx.Limits(max_connections=2)
    ) as client:

        async def consumer() -> None:
            while True:
                try:
                    if await worker_once(sessions, client, settings):
                        continue
                except Exception as exc:
                    logger.warning("analysis_worker_error type=%s", type(exc).__name__)
                await asyncio.sleep(1)

        # Bound model/DB pressure on the 2 GiB VPS. Each consumer has its own DB sessions.
        async with asyncio.TaskGroup() as group:
            group.create_task(consumer())
            group.create_task(consumer())
