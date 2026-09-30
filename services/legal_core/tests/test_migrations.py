import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from legal_core.database import database_url
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import ProgrammingError

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
            "case_escalation_messages",
            "case_escalation_workflow_events",
            "analysis_jobs",
            "case_analysis_runs",
            "case_analysis_claims",
            "case_retention_events",
            "cases",
            "case_facts",
            "case_reports",
            "audit_events",
            "idempotency_records",
            "telegram_case_workflows",
            "telegram_intake_drafts",
            "case_materials",
            "reference_evaluation_access_grants",
            "reference_evaluation_cases",
            "reference_evaluation_case_versions",
            "reference_evaluation_review_events",
            "legal_sources",
            "legal_documents",
            "legal_versions",
            "legal_fragments",
            "legal_approval_events",
            "legal_update_review_items",
            "legal_update_runs",
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
                    "'telegram_case_workflows','telegram_intake_drafts','subscription_entitlements',"
                    "'case_materials','reference_evaluation_access_grants',"
                    "'reference_evaluation_cases','reference_evaluation_case_versions',"
                    "'reference_evaluation_review_events',"
                    "'subscription_entitlement_events','case_risk_assessments','case_escalations',"
                    "'case_escalation_messages','case_escalation_workflow_events','analysis_jobs',"
                    "'case_analysis_runs','case_analysis_claims','case_retention_events')"
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
                    "'case_risk_assessments','case_escalations','case_analysis_runs',"
                    "'case_analysis_claims','case_escalation_messages','legal_update_review_items',"
                    "'legal_update_runs','case_escalation_workflow_events',"
                    "'reference_evaluation_review_events')"
                )
            ).scalars()
            legal_guard_triggers = set(
                connection.execute(
                    text(
                        "SELECT tgname FROM pg_trigger "
                        "WHERE NOT tgisinternal AND tgname IN "
                        "('legal_approval_events_validate_insert',"
                        "'legal_fragments_append_only','legal_sources_protect_identity',"
                        "'legal_versions_protect_content',"
                        "'reference_evaluation_review_events_guard')"
                    )
                ).scalars()
            )
            legal_guard_functions = set(
                connection.execute(
                    text(
                        "SELECT proname FROM pg_proc WHERE proname IN "
                        "('legal_canonical_jsonb','legal_regression_result_sha256',"
                        "'legal_approval_event_is_current',"
                        "'purge_expired_telegram_intake_drafts',"
                        "'purge_expired_case_content',"
                        "'claim_expired_case_materials',"
                        "'finalize_expired_case_material_deletion',"
                        "'release_expired_case_material_deletion',"
                        "'claim_expired_reference_evaluation_versions',"
                        "'finalize_expired_reference_evaluation_version',"
                        "'release_expired_reference_evaluation_version')"
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
                "telegram_intake_drafts",
                "case_materials",
                "reference_evaluation_access_grants",
                "reference_evaluation_cases",
                "reference_evaluation_case_versions",
                "reference_evaluation_review_events",
                "subscription_entitlements",
                "subscription_entitlement_events",
                "case_risk_assessments",
                "case_escalations",
                "case_escalation_messages",
                "case_escalation_workflow_events",
                "analysis_jobs",
                "case_analysis_runs",
                "case_analysis_claims",
                "case_retention_events",
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
                "case_escalation_messages",
                "case_escalation_workflow_events",
                "case_analysis_runs",
                "case_analysis_claims",
                "legal_update_review_items",
                "legal_update_runs",
                "reference_evaluation_review_events",
            }
            assert legal_guard_triggers == {
                "legal_approval_events_validate_insert",
                "legal_fragments_append_only",
                "legal_sources_protect_identity",
                "legal_versions_protect_content",
                "reference_evaluation_review_events_guard",
            }
            assert legal_guard_functions == {
                "legal_canonical_jsonb",
                "legal_regression_result_sha256",
                "legal_approval_event_is_current",
                "purge_expired_telegram_intake_drafts",
                "purge_expired_case_content",
                "claim_expired_case_materials",
                "finalize_expired_case_material_deletion",
                "release_expired_case_material_deletion",
                "claim_expired_reference_evaluation_versions",
                "finalize_expired_reference_evaluation_version",
                "release_expired_reference_evaluation_version",
            }

        with engine.connect() as connection:
            v2_evidence_exists = bool(
                connection.scalar(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM legal_versions "
                        "WHERE artifact_kind = 'THIRD_PARTY_VERIFIED_COPY' "
                        "UNION ALL "
                        "SELECT 1 FROM legal_approval_events "
                        "WHERE policy_version = 'dental-legal-approval.v2'"
                        ")"
                    )
                )
            )
        if v2_evidence_exists:
            with pytest.raises(ProgrammingError, match="cannot downgrade"):
                command.downgrade(config, "f19b4c6e7d20")
        else:
            command.downgrade(config, "f19b4c6e7d20")
            remaining = set(inspect(engine).get_table_names())
            assert "subscription_entitlements" not in remaining
            assert "subscription_entitlement_events" not in remaining
    finally:
        engine.dispose()
        command.upgrade(config, "head")
