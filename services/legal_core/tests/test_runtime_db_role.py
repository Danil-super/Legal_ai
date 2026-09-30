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

                reference_review_access = (
                    await connection.execute(
                        text(
                            "SELECT "
                            "has_table_privilege(current_user, "
                            "'public.legal_reference_review_events', 'SELECT') AS can_select, "
                            "has_table_privilege(current_user, "
                            "'public.legal_reference_review_events', 'INSERT') AS can_insert, "
                            "has_table_privilege(current_user, "
                            "'public.legal_reference_review_events', 'UPDATE') AS can_update, "
                            "has_table_privilege(current_user, "
                            "'public.legal_reference_review_events', 'DELETE') AS can_delete, "
                            "has_function_privilege('public', "
                            "'public.guard_legal_reference_review_event()', 'EXECUTE') "
                            "AS public_can_execute_guard, "
                            "has_function_privilege(current_user, "
                            "'public.guard_legal_reference_review_event()', 'EXECUTE') "
                            "AS runtime_can_execute_guard"
                        )
                    )
                ).mappings().one()
                assert reference_review_access == {
                    "can_select": True,
                    "can_insert": True,
                    "can_update": False,
                    "can_delete": False,
                    "public_can_execute_guard": False,
                    "runtime_can_execute_guard": True,
                }

                evaluation_review_access = (
                    await connection.execute(
                        text(
                            "SELECT "
                            "has_table_privilege(current_user, "
                            "'public.reference_evaluation_review_events', 'SELECT') "
                            "AS can_select, "
                            "has_table_privilege(current_user, "
                            "'public.reference_evaluation_review_events', 'INSERT') "
                            "AS can_insert, "
                            "has_table_privilege(current_user, "
                            "'public.reference_evaluation_review_events', 'UPDATE') "
                            "AS can_update, "
                            "has_table_privilege(current_user, "
                            "'public.reference_evaluation_review_events', 'DELETE') "
                            "AS can_delete, "
                            "has_function_privilege('public', "
                            "'public.guard_reference_evaluation_review_event()', 'EXECUTE') "
                            "AS public_can_execute_guard, "
                            "has_function_privilege(current_user, "
                            "'public.guard_reference_evaluation_review_event()', 'EXECUTE') "
                            "AS runtime_can_execute_guard"
                        )
                    )
                ).mappings().one()
                assert evaluation_review_access == {
                    "can_select": True,
                    "can_insert": True,
                    "can_update": False,
                    "can_delete": False,
                    "public_can_execute_guard": False,
                    "runtime_can_execute_guard": True,
                }

                with pytest.raises(DBAPIError, match="permission denied"):
                    await connection.execute(
                        text("CREATE TABLE runtime_role_must_not_create (id int)")
                    )
                await connection.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
