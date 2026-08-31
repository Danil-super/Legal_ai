import asyncio
import os

import pytest
from legal_core.database import database_url
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run runtime database role tests",
)


def test_runtime_database_identity_is_not_privileged() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        try:
            async with engine.connect() as connection:
                identity = (
                    await connection.execute(
                        text(
                            "SELECT current_user, rolsuper, rolcreatedb, rolcreaterole, "
                            "rolreplication, rolbypassrls "
                            "FROM pg_roles WHERE rolname = current_user"
                        )
                    )
                ).mappings().one()
                assert identity["current_user"] == os.environ["POSTGRES_APP_USER"]
                assert identity["rolsuper"] is False
                assert identity["rolcreatedb"] is False
                assert identity["rolcreaterole"] is False
                assert identity["rolreplication"] is False
                assert identity["rolbypassrls"] is False

                with pytest.raises(DBAPIError, match="permission denied"):
                    await connection.execute(
                        text("CREATE TABLE runtime_role_must_not_create (id int)")
                    )
                await connection.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
