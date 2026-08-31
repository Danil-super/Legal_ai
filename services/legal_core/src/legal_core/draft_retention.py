"""Bounded retention for unsubmitted Telegram intake drafts."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def purge_expired_intake_drafts(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Delete only drafts whose server-calculated 30-day retention has elapsed."""

    async with session_factory() as session:
        result = await session.execute(text("SELECT public.purge_expired_telegram_intake_drafts()"))
        await session.commit()
    return int(result.scalar_one())
