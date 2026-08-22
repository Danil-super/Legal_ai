from datetime import UTC, datetime
from uuid import UUID

from legal_core.contracts import CaseStatus, FactKey
from legal_core.reports import build_intake_report, render_report_pdf


def test_blocked_intake_report_has_one_canonical_safe_shape() -> None:
    report = build_intake_report(
        report_id=UUID("00000000-0000-0000-0000-000000000010"),
        case_id=UUID("00000000-0000-0000-0000-000000000020"),
        public_number="DL-2026-000001",
        case_status=CaseStatus.ANALYSIS_BLOCKED,
        report_version=1,
        generated_at=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
        facts={
            FactKey.PROBLEM_SUMMARY: "Синтетический обезличенный пример.",
            FactKey.INCIDENT_TYPES: ["CROWN_PROBLEM"],
        },
        missing_facts=[],
    )

    payload = report.model_dump(mode="json", by_alias=True)
    assert payload["schemaVersion"] == "dental-case-report.v1"
    assert payload["summary"]["analysisAvailability"] == {
        "status": "BLOCKED",
        "reasonCode": "LEGAL_CORPUS_NOT_READY",
    }
    assert payload["recommendations"] == {"status": "NOT_AVAILABLE", "items": []}
    assert payload["draftResponse"]["text"] is None
    assert payload["draftResponse"]["humanApprovalRequired"] is True
    assert payload["legalBasis"] == {"status": "NOT_AVAILABLE", "sources": []}


def test_pdf_is_deterministic_and_uses_report_snapshot() -> None:
    report = build_intake_report(
        report_id=UUID("00000000-0000-0000-0000-000000000010"),
        case_id=UUID("00000000-0000-0000-0000-000000000020"),
        public_number="DL-2026-000001",
        case_status=CaseStatus.ANALYSIS_BLOCKED,
        report_version=1,
        generated_at=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
        facts={FactKey.PROBLEM_SUMMARY: "Синтетический обезличенный пример."},
        missing_facts=[],
    )

    first = render_report_pdf(report)
    second = render_report_pdf(report)

    assert first.startswith(b"%PDF-")
    assert first == second
    assert len(first) > 1_000

