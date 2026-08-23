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
        "risk_policy_versions",
        "risk_policy_events",
        "case_risk_assessments",
        "case_escalations",
        "case_analysis_runs",
        "case_analysis_claims",
        "legal_sources",
        "legal_documents",
        "legal_versions",
        "legal_fragments",
        "legal_approval_events",
        "legal_update_review_items",
        "legal_update_runs",
    }

    assert required <= set(Base.metadata.tables)


def test_legal_update_review_queue_is_checksum_bound_and_global() -> None:
    queue = Base.metadata.tables["legal_update_review_items"]

    assert "clinic_id" not in queue.columns
    assert {
        "source_id",
        "document_id",
        "previous_legal_version_id",
        "candidate_legal_version_id",
        "raw_sha256",
        "normalized_sha256",
        "fragments_sha256",
        "structural_diff_sha256",
        "structural_diff_json",
        "candidate_sha256",
        "status",
    } <= set(queue.columns.keys())


def test_legal_update_runs_are_global_and_traceable_without_raw_error_text() -> None:
    runs = Base.metadata.tables["legal_update_runs"]

    assert "clinic_id" not in runs.columns
    assert {
        "source_id",
        "document_id",
        "review_item_id",
        "idempotency_sha256",
        "result_sha256",
        "status",
        "failure_code",
    } <= set(runs.columns.keys())
    assert "error_text" not in runs.columns


def test_every_tenant_owned_table_has_clinic_id() -> None:
    for table_name in (
        "clinic_users",
        "cases",
        "case_facts",
        "case_reports",
        "audit_events",
        "telegram_case_workflows",
        "case_risk_assessments",
        "case_escalations",
        "case_analysis_runs",
        "case_analysis_claims",
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
