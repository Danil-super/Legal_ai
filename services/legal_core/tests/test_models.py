from legal_core.models import Base


def test_required_domain_tables_are_declared() -> None:
    required = {
        "clinics",
        "users",
        "clinic_users",
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
    }

    assert required <= set(Base.metadata.tables)


def test_every_tenant_owned_table_has_clinic_id() -> None:
    for table_name in (
        "clinic_users",
        "cases",
        "case_facts",
        "case_reports",
        "audit_events",
        "telegram_case_workflows",
    ):
        assert "clinic_id" in Base.metadata.tables[table_name].columns


def test_telegram_workflow_report_foreign_key_binds_tenant_and_case() -> None:
    workflow = Base.metadata.tables["telegram_case_workflows"]

    assert any(
        foreign_key.referred_table.name == "case_reports"
        and tuple(element.parent.name for element in foreign_key.elements)
        == ("clinic_id", "case_id", "report_id")
        and tuple(element.column.name for element in foreign_key.elements)
        == ("clinic_id", "case_id", "id")
        for foreign_key in workflow.foreign_key_constraints
    )
