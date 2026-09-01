from datetime import UTC, date, datetime
from uuid import UUID

from legal_core.contracts import CaseStatus, FactKey
from legal_core.legal_retrieval import ApprovedLegalFragment
from legal_core.reports import build_analysis_report, build_intake_report, render_report_pdf
from legal_core.risk_engine import RiskAssessment, RiskLevel


FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000030")


def _evidence() -> ApprovedLegalFragment:
    return ApprovedLegalFragment(
        fragment_id=FRAGMENT_ID,
        version_id=UUID("00000000-0000-0000-0000-000000000031"),
        document_id=UUID("00000000-0000-0000-0000-000000000032"),
        article=None,
        part=None,
        point="34",
        structural_path="point:34",
        fragment_text="Синтетический фрагмент нормы.",
        text_sha256="a" * 64,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        source_url="https://publication.pravo.gov.ru/synthetic",
        raw_sha256="b" * 64,
        document_title="Синтетический акт",
        issuer="Synthetic authority",
        official_number="1",
        version_date=date(2026, 1, 1),
        publication_date=date(2026, 1, 1),
    )


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
    assert payload["risk"] is None
    assert payload["analysis"] is None


def test_verified_low_risk_report_contains_safe_draft_actions_and_sources() -> None:
    report = build_analysis_report(
        report_id=UUID("00000000-0000-0000-0000-000000000010"),
        analysis_run_id=UUID("00000000-0000-0000-0000-000000000011"),
        case_id=UUID("00000000-0000-0000-0000-000000000020"),
        public_number="DL-2026-000001",
        case_status=CaseStatus.REPORT_READY,
        report_version=2,
        generated_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
        as_of_date=date(2026, 8, 31),
        facts={
            FactKey.PROBLEM_SUMMARY: "Пациент сообщил о сколе винира.",
            FactKey.INCIDENT_TYPES: ["VENEER_CHIP"],
        },
        missing_facts=[],
        risk=RiskAssessment(
            level=RiskLevel.LOW,
            reason_codes=("NO_ESCALATION_TRIGGER",),
            policy_version="dental-risk.v1",
            fact_snapshot_sha256="c" * 64,
            external_draft_allowed=True,
        ),
        evidence_trace_sha256="d" * 64,
        evidence=[_evidence()],
        verified_action_items=["Зафиксировать обращение и предложить осмотр."],
    )

    payload = report.model_dump(mode="json", by_alias=True)
    assert payload["summary"]["analysisAvailability"] == {
        "status": "READY",
        "reasonCode": None,
    }
    assert payload["risk"]["level"] == "LOW"
    assert payload["recommendations"] == {
        "status": "AVAILABLE",
        "items": ["Зафиксировать обращение и предложить осмотр."],
    }
    assert payload["draftResponse"]["status"] == "AVAILABLE"
    assert payload["draftResponse"]["reasonCode"] is None
    assert payload["draftResponse"]["humanApprovalRequired"] is True
    assert "не будем делать выводы о причинах" in payload["draftResponse"]["text"]
    assert "Пациент сообщил о сколе винира" not in payload["draftResponse"]["text"]
    assert payload["legalBasis"]["status"] == "AVAILABLE"
    assert payload["legalBasis"]["sources"][0]["fragmentId"] == str(FRAGMENT_ID)


def test_high_risk_analysis_requires_escalation_and_human_review_for_draft() -> None:
    report = build_analysis_report(
        report_id=UUID("00000000-0000-0000-0000-000000000010"),
        analysis_run_id=UUID("00000000-0000-0000-0000-000000000011"),
        case_id=UUID("00000000-0000-0000-0000-000000000020"),
        public_number="DL-2026-000001",
        case_status=CaseStatus.ESCALATION_REQUIRED,
        report_version=2,
        generated_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
        as_of_date=date(2026, 8, 31),
        facts={FactKey.PROBLEM_SUMMARY: "Получена письменная претензия."},
        missing_facts=[],
        risk=RiskAssessment(
            level=RiskLevel.HIGH,
            reason_codes=("FORMAL_CLAIM_RECEIVED",),
            policy_version="dental-risk.v1",
            fact_snapshot_sha256="c" * 64,
            external_draft_allowed=False,
        ),
        evidence_trace_sha256="d" * 64,
        evidence=[_evidence()],
        verified_action_items=["Передать кейс ответственному юристу."],
    )

    assert report.risk is not None
    assert report.risk.escalation_required is True
    assert report.draft_response.status == "BLOCKED"
    assert report.draft_response.reason_code == "HUMAN_LEGAL_REVIEW_REQUIRED"


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


def test_verified_analysis_pdf_is_deterministic() -> None:
    report = build_analysis_report(
        report_id=UUID("00000000-0000-0000-0000-000000000010"),
        analysis_run_id=UUID("00000000-0000-0000-0000-000000000011"),
        case_id=UUID("00000000-0000-0000-0000-000000000020"),
        public_number="DL-2026-000001",
        case_status=CaseStatus.REPORT_READY,
        report_version=2,
        generated_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
        as_of_date=date(2026, 8, 31),
        facts={FactKey.PROBLEM_SUMMARY: "Синтетический обезличенный пример."},
        missing_facts=[],
        risk=RiskAssessment(
            level=RiskLevel.LOW,
            reason_codes=("NO_ESCALATION_TRIGGER",),
            policy_version="dental-risk.v1",
            fact_snapshot_sha256="c" * 64,
            external_draft_allowed=True,
        ),
        evidence_trace_sha256="d" * 64,
        evidence=[_evidence()],
        verified_action_items=["Зафиксировать обращение."],
    )

    first = render_report_pdf(report)
    second = render_report_pdf(report)

    assert first.startswith(b"%PDF-")
    assert first == second
