"""Read-only owner diagnostics; never confuse prerequisites with a successful model run."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core import __version__
from legal_core.analysis_job_worker import WorkerSettings
from legal_core.case_api import TelegramUserId, _require_clinic_owner, resolve_actor
from legal_core.contracts import ContractModel
from legal_core.legal_retrieval import ApprovedLegalCorpusRepository
from legal_core.models import CaseReport
from legal_core.risk_policy_repository import ApprovedRiskPolicyRepository

RuntimeStatus = Literal["DISABLED", "CONFIG_INVALID", "UNREACHABLE", "REACHABLE"]


class AnalysisDiagnostics(ContractModel):
    checked_at: datetime = Field(alias="checkedAt")
    application_version: str = Field(alias="applicationVersion")
    source_revision: str | None = Field(default=None, alias="sourceRevision")
    runtime: RuntimeStatus
    risk_policy: Literal["APPROVED", "NOT_READY"] = Field(alias="riskPolicy")
    legal_corpus_today: Literal["PRESENT", "EMPTY"] = Field(alias="legalCorpusToday")
    last_successful_report_at: datetime | None = Field(alias="lastSuccessfulReportAt")
    model_execution: Literal["NOT_TESTED"] = Field(default="NOT_TESTED", alias="modelExecution")
    case_coverage: Literal["NOT_TESTED"] = Field(default="NOT_TESTED", alias="caseCoverage")


def _source_revision() -> str | None:
    value = os.getenv("DENTAL_RELEASE_SHA", "")
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


async def inspect_analysis_runtime() -> RuntimeStatus:
    try:
        settings = WorkerSettings.load()
    except ValueError:
        return "CONFIG_INVALID"
    if settings is None:
        return "DISABLED"
    try:
        # Fixed server configuration only: the request cannot supply a probe destination.
        # No provider API keys, model calls, embedding requests or prompts are involved.
        async with asyncio.timeout(3), httpx.AsyncClient(
            timeout=2, trust_env=False, follow_redirects=False,
        ) as client:
            async with client.stream("GET", f"{settings.url}/health/live") as response:
                if response.status_code != 200:
                    return "UNREACHABLE"
                # A liveness HTTP response is deliberately reported only as REACHABLE.
                # It is not proof that either provider can execute a legal analysis.
                return "REACHABLE"
    except (httpx.HTTPError, TimeoutError):
        return "UNREACHABLE"


async def collect_analysis_diagnostics(
    session: AsyncSession, telegram_user_id: int,
) -> AnalysisDiagnostics:
    actor = await resolve_actor(session, telegram_user_id)
    _require_clinic_owner(actor)
    # Authorization precedes both shared metadata reads and the internal network probe.
    checked_at = datetime.now(UTC)
    policy: Literal["APPROVED", "NOT_READY"] = "APPROVED"
    try:
        await ApprovedRiskPolicyRepository(session).get()
    except (LookupError, ValueError):
        policy = "NOT_READY"
    documents = await ApprovedLegalCorpusRepository(session).list_documents(
        as_of_date=checked_at.date(), limit=1,
    )
    last_report = await session.scalar(
        select(func.max(CaseReport.created_at)).where(
            CaseReport.clinic_id == actor.clinic_id,
            CaseReport.status.in_(["REPORT_READY", "ESCALATION_REQUIRED"]),
        )
    )
    return AnalysisDiagnostics(
        checkedAt=checked_at,
        applicationVersion=__version__,
        sourceRevision=_source_revision(),
        runtime=await inspect_analysis_runtime(),
        riskPolicy=policy,
        legalCorpusToday="PRESENT" if documents else "EMPTY",
        lastSuccessfulReportAt=last_report,
    )


def create_analysis_diagnostics_router(
    sessions: async_sessionmaker[AsyncSession],
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["analysis-diagnostics"])

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    session_dep = Depends(session_dependency)

    @router.get("/analysis-diagnostics", response_model=AnalysisDiagnostics)
    async def diagnostics(
        telegram_user_id: TelegramUserId,
        session: AsyncSession = session_dep,
    ) -> AnalysisDiagnostics:
        return await collect_analysis_diagnostics(session, telegram_user_id)

    return router
