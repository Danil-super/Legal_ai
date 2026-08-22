"""Database configuration shared by the service, CLI and migrations."""

import os
from collections.abc import AsyncIterator, Mapping

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def database_url(environment: Mapping[str, str] | None = None) -> URL:
    source = os.environ if environment is None else environment
    return URL.create(
        drivername="postgresql+psycopg",
        username=source.get("POSTGRES_USER", "dental_legal"),
        password=source.get("POSTGRES_PASSWORD", ""),
        host=source.get("POSTGRES_HOST", "localhost"),
        port=int(source.get("POSTGRES_PORT", "5432")),
        database=source.get("POSTGRES_DB", "dental_legal"),
    )


def create_engine() -> AsyncEngine:
    # One AsyncSession is created per request. A shared AsyncSession is not concurrency-safe.
    # Source: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks
    return create_async_engine(database_url(), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
