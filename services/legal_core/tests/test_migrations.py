import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from legal_core.database import database_url
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL migration tests",
)

ROOT = Path(__file__).parents[3]


def alembic_config() -> Config:
    return Config(str(ROOT / "alembic.ini"))


def test_upgrade_security_and_downgrade_roundtrip() -> None:
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    sync_url = database_url().set(drivername="postgresql+psycopg")
    engine = create_engine(sync_url)
    try:
        table_names = set(inspect(engine).get_table_names())
        assert {
            "clinics",
            "users",
            "clinic_users",
            "cases",
            "case_facts",
            "case_reports",
            "audit_events",
            "idempotency_records",
            "legal_sources",
            "legal_documents",
            "legal_versions",
            "legal_fragments",
        } <= table_names

        with engine.connect() as connection:
            secured = connection.execute(
                text(
                    "SELECT relname FROM pg_class "
                    "WHERE relrowsecurity AND relname IN "
                    "('cases','case_facts','case_reports','audit_events','idempotency_records')"
                )
            ).scalars()
            triggers = connection.execute(
                text(
                    "SELECT c.relname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid=t.tgrelid "
                    "WHERE NOT t.tgisinternal AND c.relname IN ('case_facts','case_reports')"
                )
            ).scalars()
            assert set(secured) == {
                "cases",
                "case_facts",
                "case_reports",
                "audit_events",
                "idempotency_records",
            }
            assert set(triggers) == {"case_facts", "case_reports"}

        command.downgrade(config, "base")
        remaining = set(inspect(engine).get_table_names())
        assert not (table_names - {"alembic_version"}).intersection(remaining)
    finally:
        engine.dispose()
        command.upgrade(config, "head")
