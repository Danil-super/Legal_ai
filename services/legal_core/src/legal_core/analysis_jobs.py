"""Durable analysis intent, actor-scoped status and fenced worker leases.

No facts, model output or PDF bytes are duplicated in this queue. Completed responses are
read from the existing tenant-scoped analysis idempotency record and follow its retention.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header
from pydantic import Field
from sqlalchemy import BigInteger, DateTime, ForeignKeyConstraint, Integer, String, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from legal_core.analysis_contracts import AnalysisSubmissionResponse
from legal_core.analysis_safe_stop import require_analysis_running
from legal_core.case_api import (
    ActorContext,
    ApiError,
    TelegramUserId,
    _audit,
    _require_finalized_case,
    _tenant_case,
    resolve_actor,
)
from legal_core.contracts import ContractModel
from legal_core.models import Base, Case, IdempotencyRecord

JobState = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["clinic_id", "case_id"], ["cases.clinic_id", "cases.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["clinic_id", "actor_membership_id"], ["clinic_users.clinic_id", "clinic_users.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID]
    case_id: Mapped[UUID]
    actor_membership_id: Mapped[UUID]
    message_id: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[JobState] = mapped_column(String(20), server_default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AnalysisJobRequest(ContractModel):
    message_id: int = Field(alias="messageId", gt=0)


class AnalysisJobResponse(ContractModel):
    job_id: UUID = Field(alias="jobId")
    case_id: UUID = Field(alias="caseId")
    state: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    error_code: str | None = Field(default=None, alias="errorCode")
    result: AnalysisSubmissionResponse | None = None


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    clinic_id: UUID
    case_id: UUID
    actor_membership_id: UUID
    telegram_user_id: int
    lease_token: UUID


async def claim_job(sessions: async_sessionmaker[AsyncSession]) -> ClaimedJob | None:
    async with sessions() as session:
        row = (
            (await session.execute(text("SELECT * FROM public.claim_analysis_job()")))
            .mappings()
            .one_or_none()
        )
        await session.commit()
    return ClaimedJob(**row) if row is not None else None


async def job_result(session: AsyncSession, job: AnalysisJob) -> AnalysisSubmissionResponse | None:
    record = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.clinic_id == job.clinic_id,
            IdempotencyRecord.actor_membership_id == job.actor_membership_id,
            IdempotencyRecord.scope == f"cases:{job.case_id}:analysis-submissions",
            IdempotencyRecord.key == str(job.id),
            IdempotencyRecord.state == "SUCCEEDED",
        )
    )
    if record is None or record.response_json is None:
        return None
    return AnalysisSubmissionResponse.model_validate(record.response_json)


async def require_job_lease(
    session: AsyncSession,
    actor: ActorContext,
    case_id: UUID,
    job_id: UUID,
    token: UUID,
) -> AnalysisJob:
    job = await session.scalar(
        select(AnalysisJob)
        .where(
            AnalysisJob.id == job_id,
            AnalysisJob.clinic_id == actor.clinic_id,
            AnalysisJob.case_id == case_id,
            AnalysisJob.actor_membership_id == actor.membership_id,
        )
        .with_for_update()
    )
    if (
        job is None
        or job.state != "RUNNING"
        or job.lease_token != token
        or job.lease_until is None
        or job.lease_until <= datetime.now(UTC)
    ):
        raise ApiError(
            status_code=409,
            code="ANALYSIS_JOB_LEASE_EXPIRED",
            message="Analysis job lease is no longer current",
        )
    return job


async def _response(session: AsyncSession, job: AnalysisJob) -> AnalysisJobResponse:
    result = await job_result(session, job)
    return AnalysisJobResponse(
        jobId=job.id,
        caseId=job.case_id,
        state="SUCCEEDED" if result else job.state,
        errorCode=None if result else job.error_code,
        result=result,
    )


def create_analysis_jobs_router(sessions: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["analysis-jobs"])

    async def session_dependency() -> Any:
        async with sessions() as session:
            yield session

    session_dep = Depends(session_dependency)

    async def owned_job(session: AsyncSession, actor: ActorContext, job_id: UUID) -> AnalysisJob:
        job = await session.scalar(
            select(AnalysisJob).where(
                AnalysisJob.id == job_id,
                AnalysisJob.clinic_id == actor.clinic_id,
                AnalysisJob.actor_membership_id == actor.membership_id,
            )
        )
        if job is None:
            raise ApiError(status_code=404, code="ANALYSIS_JOB_NOT_FOUND", message="Job not found")
        case = await _tenant_case(session, actor, job.case_id)
        if case.retention_due_at is not None and case.retention_due_at <= datetime.now(UTC):
            raise ApiError(
                status_code=410, code="CASE_CONTENT_EXPIRED", message="Case content expired"
            )
        return job

    @router.post(
        "/cases/{case_id}/analysis-jobs", response_model=AnalysisJobResponse, status_code=202
    )
    async def enqueue(
        case_id: UUID,
        payload: AnalysisJobRequest,
        telegram_user_id: TelegramUserId,
        session: AsyncSession = session_dep,
    ) -> AnalysisJobResponse:
        from legal_core.analysis_job_worker import WorkerSettings

        actor = await resolve_actor(session, telegram_user_id)
        case = await _tenant_case(session, actor, case_id)
        _require_finalized_case(case)
        if case.retention_due_at is not None and case.retention_due_at <= datetime.now(UTC):
            raise ApiError(
                status_code=410, code="CASE_CONTENT_EXPIRED", message="Case content expired"
            )
        if actor.role == "CLINIC_ADMIN" and case.created_by_membership_id != actor.membership_id:
            raise ApiError(status_code=404, code="CASE_NOT_FOUND", message="Case not found")
        require_analysis_running()
        if WorkerSettings.load() is None:
            raise ApiError(
                status_code=503,
                code="ANALYSIS_SERVICE_UNAVAILABLE",
                message="Analysis worker is not configured",
            )
        # Serialize intent creation before checking for an existing active job.
        await session.execute(
            select(Case.id)
            .where(
                Case.id == case_id,
                Case.clinic_id == actor.clinic_id,
            )
            .with_for_update()
        )
        latest = await session.scalar(
            select(AnalysisJob)
            .where(
                AnalysisJob.clinic_id == actor.clinic_id,
                AnalysisJob.case_id == case_id,
            )
            .order_by(AnalysisJob.created_at.desc(), AnalysisJob.id.desc())
            .limit(1)
        )
        if latest is not None and latest.state in {"QUEUED", "RUNNING", "SUCCEEDED"}:
            if latest.actor_membership_id != actor.membership_id:
                raise ApiError(
                    status_code=409,
                    code="ANALYSIS_ALREADY_REQUESTED",
                    message="Analysis already requested by another clinic actor",
                )
            return await _response(session, latest)
        from legal_core.analysis_api import _require_analysis_eligible_case

        _require_analysis_eligible_case(case)
        job = AnalysisJob(
            clinic_id=actor.clinic_id,
            case_id=case_id,
            actor_membership_id=actor.membership_id,
            message_id=payload.message_id,
        )
        session.add(job)
        await session.flush()
        session.add(
            _audit(
                actor=actor,
                action="CASE_ANALYSIS_QUEUED",
                resource_type="CASE",
                resource_id=case_id,
                metadata={"jobId": str(job.id)},
            )
        )
        response = await _response(session, job)
        await session.commit()
        return response

    @router.get("/analysis-jobs/{job_id}", response_model=AnalysisJobResponse)
    async def status(
        job_id: UUID, telegram_user_id: TelegramUserId, session: AsyncSession = session_dep
    ) -> AnalysisJobResponse:
        actor = await resolve_actor(session, telegram_user_id)
        return await _response(session, await owned_job(session, actor, job_id))

    @router.post("/analysis-jobs/{job_id}/notification-ack", status_code=204)
    async def ack(
        job_id: UUID, telegram_user_id: TelegramUserId, session: AsyncSession = session_dep
    ) -> None:
        actor = await resolve_actor(session, telegram_user_id)
        job = await owned_job(session, actor, job_id)
        if job.state in {"SUCCEEDED", "FAILED"}:
            job.notified_at = datetime.now(UTC)
            await session.commit()

    @router.get("/internal/analysis-job-notifications")
    async def notifications(
        key: Annotated[str | None, Header(alias="X-Agent-Internal-Key")] = None,
        session: AsyncSession = session_dep,
    ) -> dict[str, Any]:
        expected = os.getenv("AGENT_INTERNAL_KEY", "")
        if len(expected) < 32 or key is None or not hmac.compare_digest(expected, key):
            raise ApiError(status_code=403, code="INTERNAL_ACCESS_REQUIRED", message="Forbidden")
        rows = (
            await session.execute(text("SELECT * FROM public.analysis_job_notifications()"))
        ).mappings()
        return {
            "items": [
                {
                    "jobId": row["job_id"],
                    "telegramUserId": row["telegram_user_id"],
                    "messageId": row["message_id"],
                }
                for row in rows
            ]
        }

    return router
