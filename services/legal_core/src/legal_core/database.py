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


def _database_url(
    source: Mapping[str, str],
    *,
    username: str,
    password: str,
) -> URL:
    return URL.create(
        drivername="postgresql+psycopg",
        username=username,
        password=password,
        host=source.get("POSTGRES_HOST", "localhost"),
        port=int(source.get("POSTGRES_PORT", "5432")),
        database=source.get("POSTGRES_DB", "dental_legal"),
    )


def owner_database_url(environment: Mapping[str, str] | None = None) -> URL:
    """Privileged connection used only for migrations and role provisioning."""

    source = os.environ if environment is None else environment
    return _database_url(
        source,
        username=source.get("POSTGRES_USER", "dental_legal"),
        password=source.get("POSTGRES_PASSWORD", ""),
    )


def database_url(environment: Mapping[str, str] | None = None) -> URL:
    """Least-privilege runtime connection.

    Deployments should configure a dedicated ``POSTGRES_APP_USER``. Falling back to the owner is
    retained only for backwards-compatible local/unit tooling; Compose and CI require the runtime
    identity explicitly.
    """

    source = os.environ if environment is None else environment
    owner_user = source.get("POSTGRES_USER", "dental_legal")
    owner_password = source.get("POSTGRES_PASSWORD", "")
    return _database_url(
        source,
        username=source.get("POSTGRES_APP_USER", owner_user),
        password=source.get("POSTGRES_APP_PASSWORD", owner_password),
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
