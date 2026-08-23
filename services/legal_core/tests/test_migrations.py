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


def test_upgrade_security_subscription_and_risk_migration_roundtrip() -> None:
    config = alembic_config()
    command.upgrade(config, "head")

    sync_url = database_url().set(drivername="postgresql+psycopg")
    engine = create_engine(sync_url)
    try:
        table_names = set(inspect(engine).get_table_names())
        assert {
            "clinics",
            "users",
            "clinic_users",
            "subscription_entitlements",
            "subscription_entitlement_events",
            "risk_policy_versions",
            "risk_policy_events",
            "case_risk_assessments",
            "case_escalations",
            "cases",
            "case_facts",
            "case_reports",
            "audit_events",
            "idempotency_records",
            "telegram_case_workflows",
            "legal_sources",
            "legal_documents",
            "legal_versions",
            "legal_fragments",
            "legal_approval_events",
        } <= table_names

        workflow_foreign_keys = inspect(engine).get_foreign_keys("telegram_case_workflows")
        assert any(
            foreign_key["referred_table"] == "case_reports"
            and foreign_key["constrained_columns"] == ["clinic_id", "case_id", "report_id"]
            and foreign_key["referred_columns"] == ["clinic_id", "case_id", "id"]
            for foreign_key in workflow_foreign_keys
        )

        with engine.connect() as connection:
            secured = connection.execute(
                text(
                    "SELECT relname FROM pg_class "
                    "WHERE relrowsecurity AND relname IN "
                    "('cases','case_facts','case_reports','audit_events','idempotency_records',"
                    "'telegram_case_workflows','subscription_entitlements',"
                    "'subscription_entitlement_events','case_risk_assessments','case_escalations')"
                )
            ).scalars()
            triggers = connection.execute(
                text(
                    "SELECT c.relname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid=t.tgrelid "
                    "WHERE NOT t.tgisinternal AND c.relname IN "
                    "('case_facts','case_reports','telegram_case_workflows','legal_approval_events',"
                    "'legal_sources','legal_documents','legal_versions','legal_fragments',"
                    "'subscription_entitlement_events','risk_policy_versions','risk_policy_events',"
                    "'case_risk_assessments','case_escalations')"
                )
            ).scalars()
            legal_guard_triggers = set(
                connection.execute(
                    text(
                        "SELECT tgname FROM pg_trigger "
                        "WHERE NOT tgisinternal AND tgname IN "
                        "('legal_approval_events_validate_insert',"
                        "'legal_fragments_append_only','legal_sources_protect_identity',"
                        "'legal_versions_protect_content')"
                    )
                ).scalars()
            )
            legal_guard_functions = set(
                connection.execute(
                    text(
                        "SELECT proname FROM pg_proc WHERE proname IN "
                        "('legal_canonical_jsonb','legal_regression_result_sha256',"
                        "'legal_approval_event_is_current')"
                    )
                ).scalars()
            )
            assert set(secured) == {
                "cases",
                "case_facts",
                "case_reports",
                "audit_events",
                "idempotency_records",
                "telegram_case_workflows",
                "subscription_entitlements",
                "subscription_entitlement_events",
                "case_risk_assessments",
                "case_escalations",
            }
            assert set(triggers) == {
                "case_facts",
                "case_reports",
                "telegram_case_workflows",
                "legal_approval_events",
                "legal_sources",
                "legal_documents",
                "legal_versions",
                "legal_fragments",
                "subscription_entitlement_events",
                "risk_policy_versions",
                "risk_policy_events",
                "case_risk_assessments",
                "case_escalations",
            }
            assert legal_guard_triggers == {
                "legal_approval_events_validate_insert",
                "legal_fragments_append_only",
                "legal_sources_protect_identity",
                "legal_versions_protect_content",
            }
            assert legal_guard_functions == {
                "legal_canonical_jsonb",
                "legal_regression_result_sha256",
                "legal_approval_event_is_current",
            }

        command.downgrade(config, "f19b4c6e7d20")
        remaining = set(inspect(engine).get_table_names())
        assert "subscription_entitlements" not in remaining
        assert "subscription_entitlement_events" not in remaining
    finally:
        engine.dispose()
        command.upgrade(config, "head")
