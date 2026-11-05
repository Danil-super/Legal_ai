"""Creator-scoped, paginated case/report history without raw case text or PDF bytes."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.case_api import (
    ApiError,
    TelegramUserId,
    _audit,
    _case_response,
    _require_case_intake_actor,
    _tenant_case,
    resolve_actor,
)
from legal_core.case_history_contracts import (
    PAGE_SIZE,
    CaseHistoryDetail,
    CaseHistoryPage,
    SavedReport,
)
from legal_core.models import Case, CaseEscalation, CaseReport


def create_case_history_router(sessions: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter(prefix="/v1/case-history", tags=["case-history"])

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    Session = Annotated[AsyncSession, Depends(session_dependency)]
    Offset = Annotated[int, Query(ge=0, le=100_000)]

    @router.get("", response_model=CaseHistoryPage)
    async def history(telegram_user_id: TelegramUserId, session: Session,
                      offset: Offset = 0) -> CaseHistoryPage:
        actor = await resolve_actor(session, telegram_user_id)
        _require_case_intake_actor(actor)
        now = datetime.now(UTC)
        rows = list((await session.scalars(select(Case).where(
            Case.clinic_id == actor.clinic_id, Case.created_by_membership_id == actor.membership_id,
            Case.content_purged_at.is_(None),
            (Case.retention_due_at.is_(None)) | (Case.retention_due_at > now),
        ).order_by(Case.created_at.desc(), Case.id.desc()).offset(offset)
          .limit(PAGE_SIZE + 1))).all())
        result = CaseHistoryPage(items=[_case_response(row) for row in rows[:PAGE_SIZE]],
                                 nextOffset=offset + PAGE_SIZE if len(rows) > PAGE_SIZE else None)
        session.add(_audit(actor=actor, action="CASE_HISTORY_READ", resource_type="CLINIC",
                           resource_id=actor.clinic_id, metadata={"offset": offset}))
        await session.commit()
        return result

    @router.get("/{case_id}", response_model=CaseHistoryDetail)
    async def detail(case_id: UUID, telegram_user_id: TelegramUserId, session: Session,
                     offset: Offset = 0) -> CaseHistoryDetail:
        actor = await resolve_actor(session, telegram_user_id)
        _require_case_intake_actor(actor)
        case = await _tenant_case(session, actor, case_id)
        if case.created_by_membership_id != actor.membership_id:
            raise ApiError(status_code=404, code="CASE_NOT_FOUND", message="Case not found")
        rows = (await session.execute(select(
            CaseReport.id, CaseReport.report_version, CaseReport.created_at,
            CaseReport.report_json["summary"]["analysisAvailability"]["status"].as_string(),
            CaseReport.report_json["analysis"]["verifierStatus"].as_string(),
        ).where(CaseReport.clinic_id == actor.clinic_id, CaseReport.case_id == case.id)
          .order_by(CaseReport.report_version.desc()).offset(offset).limit(PAGE_SIZE + 1))).all()
        escalation = await session.scalar(select(CaseEscalation.id).where(
            CaseEscalation.clinic_id == actor.clinic_id, CaseEscalation.case_id == case.id,
        ).order_by(CaseEscalation.created_at.desc()).limit(1))
        result = CaseHistoryDetail(
            case=_case_response(case), reports=[SavedReport(
                id=row[0], reportVersion=row[1], createdAt=row[2],
                kind="READY" if row[3] == "READY" and row[4] == "PASSED" else "CARD",
            ) for row in rows[:PAGE_SIZE]], escalationId=escalation,
            nextOffset=offset + PAGE_SIZE if len(rows) > PAGE_SIZE else None,
        )
        session.add(_audit(actor=actor, action="CASE_REPORT_HISTORY_READ", resource_type="CASE",
                           resource_id=case.id, metadata={"offset": offset}))
        await session.commit()
        return result

    return router
