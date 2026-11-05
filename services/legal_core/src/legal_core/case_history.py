"""Creator-scoped, paginated case/report history without raw case text or PDF bytes."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
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
    CaseHistoryDeletion,
    CaseHistoryDetail,
    CaseHistoryPage,
    SavedReport,
)
from legal_core.models import (
    Case,
    CaseEscalation,
    CaseMaterial,
    CaseReport,
    IdempotencyRecord,
    SubscriptionEntitlement,
    TelegramCaseWorkflow,
    TelegramIntakeDraft,
)


def create_case_history_router(sessions: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter(prefix="/v1/case-history", tags=["case-history"])

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    Session = Annotated[AsyncSession, Depends(session_dependency)]
    Offset = Annotated[int, Query(ge=0, le=100_000)]

    @router.delete("/{case_id}", response_model=CaseHistoryDeletion)
    async def delete_case(case_id: UUID, telegram_user_id: TelegramUserId,
                          session: Session) -> CaseHistoryDeletion:
        actor = await resolve_actor(session, telegram_user_id)
        _require_case_intake_actor(actor)
        # Own-case predicate is part of the lock query; even an owner cannot delete a colleague's.
        case = await session.scalar(select(Case).where(
            Case.id == case_id, Case.clinic_id == actor.clinic_id,
            Case.created_by_membership_id == actor.membership_id,
        ).with_for_update().execution_options(populate_existing=True))
        if case is None:
            raise ApiError(status_code=404, code="CASE_NOT_FOUND", message="Case not found")
        # A lock wait must not preserve withdrawn membership or transaction-start entitlement.
        if await resolve_actor(session, telegram_user_id) != actor:
            raise ApiError(status_code=403, code="ACTOR_CONTEXT_CHANGED", message="Access changed")
        entitlement = await session.scalar(select(SubscriptionEntitlement.id).where(
            SubscriptionEntitlement.clinic_id == actor.clinic_id,
            SubscriptionEntitlement.user_id == actor.user_id,
            SubscriptionEntitlement.status == "ACTIVE",
            SubscriptionEntitlement.starts_at <= func.clock_timestamp(),
            (SubscriptionEntitlement.ends_at.is_(None))
            | (SubscriptionEntitlement.ends_at > func.clock_timestamp()),
        ))
        if entitlement is None:
            raise ApiError(status_code=403, code="SUBSCRIPTION_INACTIVE", message="Access inactive")
        now = datetime.now(UTC)
        if case.content_purged_at is None and (
            case.retention_due_at is None or case.retention_due_at > now
        ):
            case.retention_due_at = now
            case.status = "CANCELLED"
            case.updated_at = now
            case.row_version += 1
            origins = select(TelegramCaseWorkflow.id).where(
                TelegramCaseWorkflow.clinic_id == actor.clinic_id,
                TelegramCaseWorkflow.case_id == case_id,
            )
            drafts = list((await session.scalars(select(TelegramIntakeDraft).where(
                TelegramIntakeDraft.clinic_id == actor.clinic_id,
                (TelegramIntakeDraft.id.in_(origins))
                | (TelegramIntakeDraft.draft_json["correctionSource"]["caseId"].as_string()
                   == str(case_id)),
            ).order_by(TelegramIntakeDraft.id).with_for_update())).all())
            draft_ids = [draft.id for draft in drafts]
            for draft in drafts:
                draft.status = "ARCHIVED"
                draft.purge_after = min(draft.purge_after, now)
                draft.draft_json = {}
                draft.revision += 1
            if draft_ids:
                # Keep receipt keys/hashes so an old callback cannot create the case again.
                await session.execute(update(IdempotencyRecord).where(
                    IdempotencyRecord.clinic_id == actor.clinic_id,
                    IdempotencyRecord.resource_type == "TELEGRAM_INTAKE_DRAFT",
                    IdempotencyRecord.resource_id.in_(draft_ids),
                ).values(response_json=None))
            await session.execute(update(CaseMaterial).where(
                CaseMaterial.clinic_id == actor.clinic_id,
                (CaseMaterial.case_id == case_id) | (CaseMaterial.draft_id.in_(draft_ids)),
                CaseMaterial.expires_at > now,
            ).values(expires_at=now))
            session.add(_audit(actor=actor, action="CASE_DELETION_REQUESTED", resource_type="CASE",
                               resource_id=case_id, metadata={}))
        # Replays preserve an earlier deadline/tombstone and do not produce duplicate audit events.
        await session.commit()
        return CaseHistoryDeletion(caseId=case_id)

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
