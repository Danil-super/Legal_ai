"""Synthetic, local-only checks for reference-workbook readiness and comparison."""

import hashlib
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZipFile

import pytest

from legal_core.contracts import CanonicalReport, CaseStatus, FactKey
from legal_core.reference_evaluation_contracts import ReferenceEvaluationDetail
from legal_core.reference_evaluation_runner import (
    ReferenceReviewManifest,
    ReferenceReviewReceipt,
    audit_workbook,
    compare_reference_report,
    main,
    write_private_report,
)
from legal_core.reports import build_intake_report

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
FIELDS = [
    "case_id",
    "scenario",
    "user_question",
    "review_status",
    "ready_for_training",
    "version_date",
    "answer_full",
    "source_urls",
]
FRAGMENT = UUID("11111111-1111-1111-1111-111111111111")
ANALYSIS_CASE = UUID("22222222-2222-2222-2222-222222222222")


def _workbook(path: Path, rows: list[dict[str, str]], *, formula: bool = False) -> None:
    worksheet = Element("worksheet", xmlns=NS)
    data = SubElement(worksheet, "sheetData")
    for number, values in enumerate([dict(zip(FIELDS, FIELDS, strict=True)), *rows], 1):
        row = SubElement(data, "row", r=str(number))
        for index, field in enumerate(FIELDS):
            cell = SubElement(row, "c", r=f"{chr(65 + index)}{number}", t="inlineStr")
            SubElement(SubElement(cell, "is"), "t").text = values.get(field, "")
            if formula and number == 2 and index == 0:
                SubElement(cell, "f").text = "1+1"
    workbook = Element("workbook", xmlns=NS, attrib={"xmlns:r": REL})
    SubElement(
        SubElement(workbook, "sheets"),
        "sheet",
        name="Для бота",
        sheetId="1",
        attrib={"r:id": "rId1"},
    )
    relationships = Element(
        "Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships"
    )
    SubElement(
        relationships,
        "Relationship",
        Id="rId1",
        Target="worksheets/sheet1.xml",
        Type=f"{REL}/worksheet",
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", tostring(workbook))
        archive.writestr("xl/_rels/workbook.xml.rels", tostring(relationships))
        archive.writestr("xl/worksheets/sheet1.xml", tostring(worksheet))


def _row(case_id: str = "case_1", **changes: str) -> dict[str, str]:
    return {
        "case_id": case_id,
        "scenario": "SYNTHETIC_NARRATIVE_CANARY Clinic received a routine document request.",
        "user_question": "How should the clinic handle a routine request?",
        "review_status": "Проверено ассистентом — готово к обучению",
        "ready_for_training": "Да",
        "version_date": "2026-10-04",
        "answer_full": "SYNTHETIC_ANSWER_CANARY Candidate answer requiring human review.",
        "source_urls": "https://example.invalid/unapproved",
        **changes,
    }


def _receipt(row_sha: str, **changes: object) -> ReferenceReviewReceipt:
    return ReferenceReviewReceipt.model_validate(
        {
            "caseId": "case_1",
            "rowSha256": row_sha,
            "referenceCaseId": str(uuid4()),
            "referenceVersion": 1,
            "referenceStatus": "APPROVED_FOR_EVALUATION",
            "referenceScenarioSha256": hashlib.sha256(
                b"Synthetic de-identified scenario."
            ).hexdigest(),
            "createdByMembershipId": str(uuid4()),
            "reviewedByMembershipId": str(uuid4()),
            "privacyReviewed": True,
            "asOfDate": "2026-10-01",
            "groupKey": "healthcare",
            "expectedRoute": "INTERNAL_DRAFT",
            "expectedRisk": "LOW",
            "expectedPolicyVersion": "dental-risk:3",
            "requiredFragmentIds": [str(FRAGMENT)],
            "requiredLegalConclusionCount": 1,
            "analysisCaseId": str(ANALYSIS_CASE),
            "factSnapshotSha256": _report().fact_snapshot_sha256,
            **changes,
        }
    )


def _reference(receipt: ReferenceReviewReceipt) -> ReferenceEvaluationDetail:
    return ReferenceEvaluationDetail(
        id=receipt.reference_case_id,
        displayName="Synthetic reference case",
        groupKey="healthcare",
        status="APPROVED_FOR_EVALUATION",
        currentVersion=1,
        hasMaterial=False,
        createdAt=datetime(2026, 10, 1, tzinfo=UTC),
        updatedAt=datetime(2026, 10, 1, tzinfo=UTC),
        asOfDate=date(2026, 10, 1),
        expectedRoute="INTERNAL_DRAFT",
        scenarioText="Synthetic de-identified scenario.",
        contentPurgedAt=None,
        createdBySelf=False,
    )


def _report(*, ready: bool = False) -> CanonicalReport:
    report = build_intake_report(
        report_id=uuid4(),
        case_id=ANALYSIS_CASE,
        public_number="SYNTHETIC-1",
        case_status=CaseStatus.ANALYSIS_BLOCKED,
        report_version=1,
        generated_at=datetime(2026, 10, 1, tzinfo=UTC),
        facts={FactKey.EVENT_SUMMARY: "SYNTHETIC_REPORT_CANARY"},
        missing_facts=[],
    ).model_dump(mode="json", by_alias=True)
    if ready:
        report.update(
            {
                "analysis": {
                    "analysisRunId": str(uuid4()),
                    "asOfDate": "2026-10-01",
                    "verifierStatus": "PASSED",
                    "evidenceTraceSha256": "a" * 64,
                },
                "risk": {
                    "level": "LOW",
                    "reasonCodes": [],
                    "policyVersion": "dental-risk:3",
                    "escalationRequired": False,
                },
                "legalBasis": {
                    "status": "AVAILABLE",
                    "sources": [
                        {
                            "fragmentId": str(FRAGMENT),
                            "documentTitle": "Synthetic law",
                            "structuralPath": "section 1",
                            "effectiveFrom": "2020-01-01",
                            "effectiveTo": None,
                            "sourceUrl": "https://example.invalid/law",
                            "textSha256": "b" * 64,
                            "rawSha256": "c" * 64,
                        }
                    ],
                },
                "legalConclusions": [
                    {
                        "claimId": "synthetic-claim",
                        "text": "Synthetic claim",
                        "evidenceFragmentIds": [str(FRAGMENT)],
                    }
                ],
                "recommendations": {"status": "AVAILABLE", "items": ["Synthetic action"]},
            }
        )
        report["summary"]["analysisAvailability"] = {"status": "READY", "reasonCode": None}
    return CanonicalReport.model_validate(report)


def test_assistant_ready_and_version_date_do_not_approve_any_of_67_cases(tmp_path: Path) -> None:
    workbook = tmp_path / "synthetic.xlsx"
    _workbook(workbook, [_row(f"case_{number}") for number in range(1, 68)])
    report = audit_workbook(workbook)
    assert report.total_cases == 67
    assert report.eligible_cases == 0
    assert report.blocker_counts["WORKSPACE_APPROVAL_REQUIRED"] == 67
    assert report.blocker_counts["PRIVACY_REVIEW_REQUIRED"] == 67
    assert report.blocker_counts["AS_OF_DATE_REQUIRED"] == 67
    serialized = report.model_dump_json(by_alias=True)
    assert "SYNTHETIC_NARRATIVE_CANARY" not in serialized
    assert "SYNTHETIC_ANSWER_CANARY" not in serialized
    assert "example.invalid" not in serialized


def test_review_receipt_is_bound_to_workbook_and_row_and_independent_review(tmp_path: Path) -> None:
    workbook = tmp_path / "synthetic.xlsx"
    _workbook(workbook, [_row()])
    initial = audit_workbook(workbook)
    receipt = _receipt(initial.cases[0].row_sha256)
    manifest = ReferenceReviewManifest(workbookSha256=initial.workbook_sha256, cases=[receipt])
    ready = audit_workbook(workbook, manifest=manifest)
    assert ready.eligible_cases == 1
    assert ready.cases[0].blockers == []
    stale = receipt.model_copy(update={"row_sha256": "0" * 64})
    assert (
        "ROW_REVIEW_MISMATCH"
        in audit_workbook(workbook, manifest=manifest.model_copy(update={"cases": [stale]}))
        .cases[0]
        .blockers
    )
    self_review = receipt.model_copy(
        update={"reviewed_by_membership_id": receipt.created_by_membership_id}
    )
    assert (
        "INDEPENDENT_REVIEW_REQUIRED"
        in audit_workbook(workbook, manifest=manifest.model_copy(update={"cases": [self_review]}))
        .cases[0]
        .blockers
    )
    with pytest.raises(ValueError, match="WORKBOOK_REVIEW_MISMATCH"):
        audit_workbook(workbook, manifest=manifest.model_copy(update={"workbook_sha256": "0" * 64}))


def test_direct_identifiers_block_even_when_a_receipt_claims_privacy_review(tmp_path: Path) -> None:
    workbook = tmp_path / "synthetic.xlsx"
    _workbook(workbook, [_row(scenario="Synthetic contact +7 999 123-45-67.")])
    initial = audit_workbook(workbook)
    manifest = ReferenceReviewManifest(
        workbookSha256=initial.workbook_sha256, cases=[_receipt(initial.cases[0].row_sha256)]
    )
    result = audit_workbook(workbook, manifest=manifest)
    assert result.eligible_cases == 0
    assert result.cases[0].blockers == ["DIRECT_IDENTIFIER_DETECTED"]
    assert "999" not in result.model_dump_json()


@pytest.mark.parametrize("kind", ["duplicate", "unsafe_id", "formula"])
def test_workbook_rejects_ambiguous_or_executable_ids(tmp_path: Path, kind: str) -> None:
    workbook = tmp_path / "synthetic.xlsx"
    rows = (
        [_row(), _row()]
        if kind == "duplicate"
        else [_row("SYNTHETIC_PRIVATE_ID +7 999 123-45-67" if kind == "unsafe_id" else "case_1")]
    )
    _workbook(workbook, rows, formula=kind == "formula")
    with pytest.raises(ValueError) as error:
        audit_workbook(workbook)
    assert "SYNTHETIC_PRIVATE_ID" not in str(error.value)
    assert "999" not in str(error.value)


def test_comparison_unavailable_never_counts_as_correct_legal_answer() -> None:
    receipt = _receipt("a" * 64)
    result = compare_reference_report(_reference(receipt), receipt, _report())
    assert result.status == "UNAVAILABLE"
    assert result.reason_codes == ["LEGAL_CORPUS_NOT_READY"]
    assert result.legal_correctness == "NOT_EVALUATED"
    assert "SYNTHETIC_REPORT_CANARY" not in result.model_dump_json()


def test_comparison_checks_risk_policy_date_and_citations() -> None:
    receipt = _receipt("a" * 64)
    reference = _reference(receipt)
    result = compare_reference_report(reference, receipt, _report(ready=True))
    assert result.status == "PASS"
    assert result.legal_correctness == "REQUIRES_LAWYER_OUTPUT_REVIEW"
    altered = _report(ready=True).model_dump(mode="json", by_alias=True)
    altered["risk"]["level"] = "HIGH"
    altered["risk"]["policyVersion"] = "dental-risk:1"
    altered["analysis"]["asOfDate"] = "2026-10-02"
    altered["legalBasis"]["sources"][0]["effectiveTo"] = "2026-10-01"
    altered["legalConclusions"] = []
    failures = compare_reference_report(reference, receipt, CanonicalReport.model_validate(altered))
    assert failures.status == "FAIL"
    assert {
        "RISK_MISMATCH",
        "POLICY_MISMATCH",
        "ANALYSIS_DATE_MISMATCH",
        "EVIDENCE_NOT_EFFECTIVE",
        "ESCALATION_FLAG_MISMATCH",
    } <= set(failures.reason_codes)


def test_unapproved_or_changed_reference_case_blocks_comparison() -> None:
    receipt = _receipt("a" * 64)
    reference = _reference(receipt).model_copy(update={"status": "READY_FOR_REVIEW"})
    result = compare_reference_report(reference, receipt, _report(ready=True))
    assert result.status == "BLOCKED"
    assert "WORKSPACE_APPROVAL_REQUIRED" in result.reason_codes
    changed = _reference(receipt).model_copy(update={"current_version": 2})
    assert (
        "REFERENCE_VERSION_MISMATCH"
        in compare_reference_report(changed, receipt, _report(ready=True)).reason_codes
    )


def test_an_unrelated_report_or_changed_facts_cannot_pass() -> None:
    receipt = _receipt("a" * 64)
    report = _report(ready=True)
    unrelated = report.model_copy(update={"case": report.case.model_copy(update={"id": uuid4()})})
    result = compare_reference_report(_reference(receipt), receipt, unrelated)
    assert result.status == "BLOCKED"
    assert result.reason_codes == ["REPORT_CASE_MISMATCH"]
    changed = report.model_copy(update={"fact_snapshot_sha256": "0" * 64})
    assert (
        "REPORT_FACTS_MISMATCH"
        in compare_reference_report(_reference(receipt), receipt, changed).reason_codes
    )


def test_modified_fact_content_and_contradictory_route_expectations_are_blocked() -> None:
    receipt = _receipt("a" * 64)
    report = _report(ready=True).model_copy(update={"facts": {"EVENT_SUMMARY": "Changed"}})
    assert (
        "REPORT_FACT_HASH_INVALID"
        in compare_reference_report(_reference(receipt), receipt, report).reason_codes
    )
    invalid_route = receipt.model_copy(update={"expected_risk": "HIGH"})
    assert (
        "ROUTE_RISK_EXPECTATION_INVALID"
        in compare_reference_report(
            _reference(receipt), invalid_route, _report(ready=True)
        ).reason_codes
    )


def test_formula_and_xml_entity_archives_are_refused_without_printing_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    os.chmod(tmp_path, 0o700)
    workbook = tmp_path / "synthetic.xlsx"
    _workbook(workbook, [_row()])
    with ZipFile(workbook, "a") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            (
                '<!DOCTYPE sst [<!ENTITY private "SYNTHETIC_PRIVATE_XML_CANARY">]>'
                '<sst xmlns="' + NS + '"><si><t>&private;</t></si></sst>'
            ),
        )
    monkeypatch.setattr(
        sys, "argv", ["runner", "audit", str(workbook), "--output", str(tmp_path / "report.json")]
    )
    assert main() == 1
    output = capsys.readouterr()
    assert output.out.strip() == "REFERENCE_INPUT_INVALID"
    assert "SYNTHETIC_PRIVATE_XML_CANARY" not in output.out + output.err
    assert not (tmp_path / "report.json").exists()


def test_ready_human_escalation_matches_and_abstain_is_separate() -> None:
    receipt = _receipt(
        "a" * 64,
        expectedRoute="HUMAN_ESCALATION",
        expectedRisk="CRITICAL",
        requiredFragmentIds=[],
        requiredLegalConclusionCount=0,
    )
    reference = _reference(receipt).model_copy(update={"expected_route": "HUMAN_ESCALATION"})
    report = _report(ready=True).model_dump(mode="json", by_alias=True)
    report["case"]["status"] = "ESCALATION_REQUIRED"
    report["risk"] = {
        "level": "CRITICAL",
        "reasonCodes": ["SYNTHETIC_TRIAGE"],
        "policyVersion": "dental-risk:3",
        "escalationRequired": True,
    }
    result = compare_reference_report(reference, receipt, CanonicalReport.model_validate(report))
    assert result.status == "PASS"
    abstain = receipt.model_copy(update={"expected_route": "ABSTAIN", "expected_risk": None})
    reference = reference.model_copy(update={"expected_route": "ABSTAIN"})
    assert compare_reference_report(reference, abstain, _report()).status == "PASS"


def test_private_output_is_exclusive_mode_600_and_only_metadata(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    workbook = tmp_path / "synthetic.xlsx"
    _workbook(workbook, [_row()])
    output = tmp_path / "report.json"
    write_private_report(output, audit_workbook(workbook))
    assert output.stat().st_mode & 0o777 == 0o600
    expected_sha = hashlib.sha256(workbook.read_bytes()).hexdigest()
    assert expected_sha == json.loads(output.read_text())["workbookSha256"]
    with pytest.raises(FileExistsError):
        write_private_report(output, audit_workbook(workbook))
    output.unlink()
    output.symlink_to(workbook)
    with pytest.raises(FileExistsError):
        write_private_report(output, audit_workbook(workbook))
