"""Read-only existence check; saved AI and human versions are never retry targets."""

import os
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.work_product_storage import CaseWorkProductVersion


def lawyer_plans_enabled() -> bool:
    return os.getenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "0").strip().lower() in {"true", "1"}


async def has_lawyer_plan(session: AsyncSession, *, clinic_id: UUID, case_id: UUID) -> bool:
    return await session.scalar(select(CaseWorkProductVersion.id).where(
        CaseWorkProductVersion.clinic_id == clinic_id,
        CaseWorkProductVersion.case_id == case_id,
    ).limit(1)) is not None
