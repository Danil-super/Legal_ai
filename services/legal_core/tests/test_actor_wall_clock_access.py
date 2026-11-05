"""Actor access uses current database time after actual transaction waits."""

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.case_api import ApiError, resolve_actor
from legal_core.database import database_url
from test_preparation_analysis import prepared_case as prepared_case
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.mark.parametrize("boundary", ["renewed", "expired"])
def test_actor_resolution_uses_current_time_after_transaction_wait(prepared_case, boundary):
    _, actor, _, _, _, _, owner = prepared_case

    async def scenario():
        runtime = create_async_engine(database_url())
        try:
            async with async_sessionmaker(runtime)() as session:
                initial = await resolve_actor(session, actor)
                await session.execute(text("SELECT pg_sleep(0.01)"))
                with owner.begin() as connection:
                    if boundary == "renewed":
                        connection.execute(text(
                            "UPDATE subscription_entitlements SET starts_at=clock_timestamp(), "
                            "ends_at=NULL WHERE user_id=:user"
                        ), {"user": initial.user_id})
                    else:
                        connection.execute(text(
                            "UPDATE subscription_entitlements "
                            "SET ends_at=clock_timestamp()+interval '0.2 seconds' "
                            "WHERE user_id=:user"
                        ), {"user": initial.user_id})
                await session.execute(text("SELECT pg_sleep(0.25)"))
                if boundary == "renewed":
                    assert await resolve_actor(session, actor) == initial
                else:
                    with pytest.raises(ApiError) as error:
                        await resolve_actor(session, actor)
                    assert error.value.code == "SUBSCRIPTION_INACTIVE"
        finally:
            await runtime.dispose()

    asyncio.run(scenario())
