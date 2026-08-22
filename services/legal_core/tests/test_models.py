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
        "legal_sources",
        "legal_documents",
        "legal_versions",
        "legal_fragments",
    }

    assert required <= set(Base.metadata.tables)


def test_every_tenant_owned_table_has_clinic_id() -> None:
    for table_name in ("clinic_users", "cases", "case_facts", "case_reports", "audit_events"):
        assert "clinic_id" in Base.metadata.tables[table_name].columns

