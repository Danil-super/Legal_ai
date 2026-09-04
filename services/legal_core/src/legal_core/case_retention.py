"""Bounded retention for confirmed case content and metadata-only audit traces."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def purge_expired_case_content(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Purge only server-due case content; the database keeps the permitted audit metadata."""

    async with session_factory() as session:
        result = await session.execute(text("SELECT public.purge_expired_case_content()"))
        await session.commit()
    return int(result.scalar_one())
