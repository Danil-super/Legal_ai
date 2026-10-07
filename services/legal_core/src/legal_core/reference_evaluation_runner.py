"""Bounded local reference evaluation; no database, model or production writes.

Workbook answers are candidates. Human review receipts select eligible examples;
the comparator checks routing/evidence metadata and never grades legal prose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from collections import Counter
from datetime import date
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]
from defusedxml.ElementTree import fromstring  # type: ignore[import-untyped]
from pydantic import Field, ValidationError, model_validator

from legal_core.contracts import CanonicalReport, ContractModel
from legal_core.pseudonymization import pseudonymize_text
from legal_core.reference_evaluation_contracts import (
    ReferenceEvaluationDetail,
    ReferenceEvaluationGroup,
    ReferenceEvaluationStatus,
    ReferenceExpectedRoute,
)

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_MAX_SOURCE_BYTES = 15_000_000
_MAX_JSON_BYTES = 5_000_000
_MAX_UNCOMPRESSED_BYTES = 64_000_000
_CASE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_CELL = re.compile(r"^([A-Z]{1,3})[1-9][0-9]{0,6}$")
_NARRATIVE_COLUMNS = frozenset(
    {
        "title",
        "scenario",
        "user_question",
        "required_facts",
        "clarifying_questions",
        "answer_short",
        "answer_full",
        "allowed_actions",
        "prohibited_actions",
        "escalation_trigger",
        "risk_notes",
        "source_note",
    }
)
RiskExpectation = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNAVAILABLE"]


class ReferenceReviewReceipt(ContractModel):
    """Private comparison metadata copied from independent reference review."""

    case_id: str = Field(alias="caseId", pattern=r"^[A-Za-z0-9_-]{1,40}$")
    row_sha256: str = Field(alias="rowSha256", pattern=r"^[0-9a-f]{64}$")
    reference_case_id: UUID = Field(alias="referenceCaseId")
    reference_version: int = Field(alias="referenceVersion", ge=1)
    reference_status: ReferenceEvaluationStatus = Field(alias="referenceStatus")
    reference_scenario_sha256: str = Field(
        alias="referenceScenarioSha256", pattern=r"^[0-9a-f]{64}$"
    )
    created_by_membership_id: UUID | None = Field(default=None, alias="createdByMembershipId")
    reviewed_by_membership_id: UUID | None = Field(default=None, alias="reviewedByMembershipId")
    privacy_reviewed: bool = Field(default=False, alias="privacyReviewed")
    as_of_date: date | None = Field(default=None, alias="asOfDate")
    group_key: ReferenceEvaluationGroup | None = Field(default=None, alias="groupKey")
    expected_route: ReferenceExpectedRoute | None = Field(default=None, alias="expectedRoute")
    expected_risk: RiskExpectation | None = Field(default=None, alias="expectedRisk")
    expected_policy_version: str | None = Field(
        default=None, alias="expectedPolicyVersion", pattern=r"^[A-Za-z0-9_.:-]{1,80}$"
    )
    required_fragment_ids: list[UUID] = Field(
        default_factory=list, alias="requiredFragmentIds", max_length=30
    )
    required_legal_conclusion_count: int = Field(
        default=0, alias="requiredLegalConclusionCount", ge=0, le=30
    )
    analysis_case_id: UUID | None = Field(default=None, alias="analysisCaseId")
    fact_snapshot_sha256: str | None = Field(
        default=None, alias="factSnapshotSha256", pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def unique_fragments(self) -> ReferenceReviewReceipt:
        if len(self.required_fragment_ids) != len(set(self.required_fragment_ids)):
            raise ValueError("DUPLICATE_EXPECTED_FRAGMENT")
        return self


class ReferenceReviewManifest(ContractModel):
    schema_version: Literal["reference-review-manifest.v1"] = Field(
        default="reference-review-manifest.v1", alias="schemaVersion"
    )
    workbook_sha256: str = Field(alias="workbookSha256", pattern=r"^[0-9a-f]{64}$")
    cases: list[ReferenceReviewReceipt] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def unique_cases(self) -> ReferenceReviewManifest:
        if len(self.cases) != len({case.case_id for case in self.cases}):
            raise ValueError("DUPLICATE_REVIEW_CASE")
        return self


class ReferenceCaseReadiness(ContractModel):
    case_id: str = Field(alias="caseId")
    row_sha256: str = Field(alias="rowSha256")
    eligible: bool
    blockers: list[str]


class ReferenceWorkbookAudit(ContractModel):
    schema_version: Literal["reference-workbook-audit.v1"] = Field(
        default="reference-workbook-audit.v1", alias="schemaVersion"
    )
    workbook_sha256: str = Field(alias="workbookSha256")
    total_cases: int = Field(alias="totalCases")
    eligible_cases: int = Field(alias="eligibleCases")
    blocker_counts: dict[str, int] = Field(alias="blockerCounts")
    cases: list[ReferenceCaseReadiness]


class ReferenceComparison(ContractModel):
    schema_version: Literal["reference-comparison.v1"] = Field(
        default="reference-comparison.v1", alias="schemaVersion"
    )
    case_id: str = Field(alias="caseId")
    reference_case_id: UUID = Field(alias="referenceCaseId")
    reference_version: int = Field(alias="referenceVersion")
    report_id: UUID | None = Field(alias="reportId")
    status: Literal["BLOCKED", "UNAVAILABLE", "PASS", "FAIL"]
    reason_codes: list[str] = Field(alias="reasonCodes")
    legal_correctness: Literal["NOT_EVALUATED", "REQUIRES_LAWYER_OUTPUT_REVIEW"] = Field(
        alias="legalCorrectness"
    )


class ReferenceComparisonInput(ContractModel):
    reference: ReferenceEvaluationDetail
    receipt: ReferenceReviewReceipt
    report: CanonicalReport | None = None


def _receipt_blockers(receipt: ReferenceReviewReceipt | None) -> list[str]:
    if receipt is None:
        return [
            "PRIVACY_REVIEW_REQUIRED",
            "WORKSPACE_APPROVAL_REQUIRED",
            "AS_OF_DATE_REQUIRED",
            "EXPECTED_ROUTE_REQUIRED",
            "REVIEWED_EXPECTATIONS_REQUIRED",
        ]
    blockers: list[str] = []
    if not receipt.privacy_reviewed:
        blockers.append("PRIVACY_REVIEW_REQUIRED")
    if receipt.reference_status != "APPROVED_FOR_EVALUATION":
        blockers.append("WORKSPACE_APPROVAL_REQUIRED")
    if (
        receipt.created_by_membership_id is None
        or receipt.reviewed_by_membership_id is None
        or receipt.created_by_membership_id == receipt.reviewed_by_membership_id
    ):
        blockers.append("INDEPENDENT_REVIEW_REQUIRED")
    if receipt.as_of_date is None:
        blockers.append("AS_OF_DATE_REQUIRED")
    if receipt.group_key is None:
        blockers.append("COVERAGE_GROUP_REQUIRED")
    if receipt.expected_route is None:
        blockers.append("EXPECTED_ROUTE_REQUIRED")
    if receipt.expected_route in {"HUMAN_ESCALATION", "INTERNAL_DRAFT"} and (
        receipt.expected_risk is None or receipt.expected_policy_version is None
    ):
        blockers.append("RISK_EXPECTATIONS_REQUIRED")
    if receipt.expected_route == "INTERNAL_DRAFT" and not receipt.required_fragment_ids:
        blockers.append("EVIDENCE_EXPECTATIONS_REQUIRED")
    if (
        receipt.expected_route == "INTERNAL_DRAFT"
        and receipt.expected_risk not in {"LOW", "MEDIUM"}
    ) or (
        receipt.expected_route == "HUMAN_ESCALATION"
        and receipt.expected_risk not in {"HIGH", "CRITICAL"}
    ):
        blockers.append("ROUTE_RISK_EXPECTATION_INVALID")
    return blockers


def _sheet_rows(path: Path) -> tuple[str, list[dict[str, str]]]:
    """Read only the bot sheet. Cached formulas and unbounded archives are refused."""

    if not path.is_file() or path.stat().st_size > _MAX_SOURCE_BYTES:
        raise ValueError("WORKBOOK_SIZE_INVALID")
    try:
        with path.open("rb") as stream:
            content = stream.read(_MAX_SOURCE_BYTES + 1)
            if len(content) > _MAX_SOURCE_BYTES:
                raise ValueError("WORKBOOK_SIZE_INVALID")
            raw_sha = hashlib.sha256(content).hexdigest()
            with ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if (
                    len(entries) > 128
                    or len(names) != len(set(names))
                    or sum(entry.file_size for entry in entries) > _MAX_UNCOMPRESSED_BYTES
                    or any(entry.flag_bits & 1 for entry in entries)
                ):
                    raise ValueError("WORKBOOK_ARCHIVE_INVALID")
                workbook = fromstring(archive.read("xl/workbook.xml"))
                relationships = fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                sheets = [
                    sheet
                    for sheet in workbook.findall(f"{_NS}sheets/{_NS}sheet")
                    if sheet.get("name") == "Для бота"
                ]
                if len(sheets) != 1:
                    raise ValueError("BOT_SHEET_REQUIRED")
                sheet_id = sheets[0].get(f"{_REL}id")
                links = [link for link in relationships if link.get("Id") == sheet_id]
                if len(links) != 1 or links[0].get("TargetMode") == "External":
                    raise ValueError("WORKBOOK_RELATIONSHIP_INVALID")
                target = links[0].get("Target", "")
                resolved = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
                if ".." in PurePosixPath(resolved).parts or not resolved.startswith("xl/"):
                    raise ValueError("WORKBOOK_RELATIONSHIP_INVALID")
                strings: list[str] = []
                if "xl/sharedStrings.xml" in names:
                    strings = [
                        "".join(item.itertext())
                        for item in fromstring(archive.read("xl/sharedStrings.xml")).findall(
                            f"{_NS}si"
                        )
                    ]
                worksheet = fromstring(archive.read(resolved))
                xml_rows = worksheet.findall(f"{_NS}sheetData/{_NS}row")
                if not xml_rows or len(xml_rows) > 1001:
                    raise ValueError("WORKBOOK_ROW_LIMIT")
                parsed_rows: list[dict[str, str]] = []
                headers: dict[str, str] = {}
                for index, row in enumerate(xml_rows):
                    cells = row.findall(f"{_NS}c")
                    if len(cells) > 100:
                        raise ValueError("WORKBOOK_CELL_LIMIT")
                    values: dict[str, str] = {}
                    for cell in cells:
                        address = _CELL.fullmatch(cell.get("r", ""))
                        if address is None or address[1] in values:
                            raise ValueError("WORKBOOK_CELL_INVALID")
                        if cell.find(f"{_NS}f") is not None:
                            raise ValueError("WORKBOOK_FORMULA_NOT_ALLOWED")
                        text = cell.find(f"{_NS}v")
                        value = text.text or "" if text is not None else ""
                        if cell.get("t") == "s":
                            if not value.isdecimal() or len(value) > 6:
                                raise ValueError("WORKBOOK_STRING_INVALID")
                            string_index = int(value)
                            if not 0 <= string_index < len(strings):
                                raise ValueError("WORKBOOK_STRING_INVALID")
                            value = strings[string_index]
                        elif cell.get("t") == "inlineStr":
                            inline = cell.find(f"{_NS}is")
                            value = "".join(inline.itertext()) if inline is not None else ""
                        if len(value) > 20_000:
                            raise ValueError("WORKBOOK_TEXT_LIMIT")
                        values[address[1]] = value
                    if index == 0:
                        headers = {column: value.strip() for column, value in values.items()}
                        required = {"case_id", "scenario", "user_question"}
                        if len(set(headers.values())) != len(headers) or not required <= set(
                            headers.values()
                        ):
                            raise ValueError("WORKBOOK_HEADERS_INVALID")
                    elif any(values.values()):
                        if set(values) - set(headers):
                            raise ValueError("WORKBOOK_COLUMN_INVALID")
                        parsed_rows.append(
                            {header: values.get(column, "") for column, header in headers.items()}
                        )
                return raw_sha, parsed_rows
    except (BadZipFile, KeyError, IndexError, DefusedXmlException, ParseError):
        raise ValueError("WORKBOOK_INVALID") from None


def audit_workbook(
    path: Path,
    *,
    manifest: ReferenceReviewManifest | None = None,
) -> ReferenceWorkbookAudit:
    raw_sha, rows = _sheet_rows(path)
    if manifest is not None and manifest.workbook_sha256 != raw_sha:
        raise ValueError("WORKBOOK_REVIEW_MISMATCH")
    receipts = {receipt.case_id: receipt for receipt in manifest.cases} if manifest else {}
    cases: list[ReferenceCaseReadiness] = []
    seen: set[str] = set()
    for row in rows:
        case_id = row["case_id"].strip()
        if _CASE_ID.fullmatch(case_id) is None:
            raise ValueError("WORKBOOK_CASE_ID_INVALID")
        if case_id in seen:
            raise ValueError("WORKBOOK_DUPLICATE_CASE_ID")
        seen.add(case_id)
        row_sha = hashlib.sha256(
            json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        receipt = receipts.get(case_id)
        blockers = _receipt_blockers(receipt)
        if receipt is not None and receipt.row_sha256 != row_sha:
            blockers.append("ROW_REVIEW_MISMATCH")
        if not row["scenario"].strip() or not row["user_question"].strip():
            blockers.append("SCENARIO_INCOMPLETE")
        if any(
            pseudonymize_text(value).changed
            for column, value in row.items()
            if column in _NARRATIVE_COLUMNS
        ):
            blockers.append("DIRECT_IDENTIFIER_DETECTED")
        cases.append(
            ReferenceCaseReadiness(
                caseId=case_id, rowSha256=row_sha, eligible=not blockers, blockers=blockers
            )
        )
    if set(receipts) - seen:
        raise ValueError("REVIEW_CASE_NOT_IN_WORKBOOK")
    counts = Counter(blocker for case in cases for blocker in case.blockers)
    return ReferenceWorkbookAudit(
        workbookSha256=raw_sha,
        totalCases=len(cases),
        eligibleCases=sum(case.eligible for case in cases),
        blockerCounts=dict(sorted(counts.items())),
        cases=cases,
    )


def compare_reference_report(
    reference: ReferenceEvaluationDetail,
    receipt: ReferenceReviewReceipt,
    report: CanonicalReport | None,
) -> ReferenceComparison:
    """Compare reviewed routing and citation requirements, not answer semantics."""

    def result(
        status: Literal["BLOCKED", "UNAVAILABLE", "PASS", "FAIL"], reasons: list[str]
    ) -> ReferenceComparison:
        return ReferenceComparison(
            caseId=receipt.case_id,
            referenceCaseId=reference.id,
            referenceVersion=reference.current_version,
            reportId=report.report_id if report is not None else None,
            status=status,
            reasonCodes=list(dict.fromkeys(reasons)),
            legalCorrectness=(
                "REQUIRES_LAWYER_OUTPUT_REVIEW" if status in {"PASS", "FAIL"} else "NOT_EVALUATED"
            ),
        )

    blockers = _receipt_blockers(receipt)
    if reference.status != "APPROVED_FOR_EVALUATION":
        blockers.append("WORKSPACE_APPROVAL_REQUIRED")
    if (
        reference.id != receipt.reference_case_id
        or reference.current_version != receipt.reference_version
    ):
        blockers.append("REFERENCE_VERSION_MISMATCH")
    if (
        reference.as_of_date != receipt.as_of_date
        or reference.group_key != receipt.group_key
        or reference.expected_route != receipt.expected_route
    ):
        blockers.append("REFERENCE_EXPECTATION_MISMATCH")
    if reference.content_purged_at is not None or reference.scenario_text is None:
        blockers.append("REFERENCE_CONTENT_UNAVAILABLE")
    elif pseudonymize_text(reference.scenario_text).changed:
        blockers.append("DIRECT_IDENTIFIER_DETECTED")
    elif hashlib.sha256(reference.scenario_text.encode()).hexdigest() != (
        receipt.reference_scenario_sha256
    ):
        blockers.append("REFERENCE_SCENARIO_MISMATCH")
    if blockers:
        return result("BLOCKED", blockers)
    if report is None:
        return result("UNAVAILABLE", ["ANALYSIS_NOT_RUN"])
    if receipt.analysis_case_id is None or receipt.fact_snapshot_sha256 is None:
        return result("BLOCKED", ["REPORT_BINDING_REQUIRED"])
    if report.case.id != receipt.analysis_case_id:
        return result("BLOCKED", ["REPORT_CASE_MISMATCH"])
    if report.fact_snapshot_sha256 != receipt.fact_snapshot_sha256:
        return result("BLOCKED", ["REPORT_FACTS_MISMATCH"])
    actual_fact_sha = hashlib.sha256(
        json.dumps(
            report.facts, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()
    if actual_fact_sha != report.fact_snapshot_sha256:
        return result("BLOCKED", ["REPORT_FACT_HASH_INVALID"])
    ready = report.summary.analysis_availability.status == "READY"
    escalated = report.risk is not None and report.risk.level in {"HIGH", "CRITICAL"}
    if receipt.expected_route == "ABSTAIN":
        if (
            ready
            or escalated
            or report.legal_conclusions
            or report.recommendations.items
            or report.draft_response.status == "AVAILABLE"
            or report.legal_basis.status == "AVAILABLE"
        ):
            return result("FAIL", ["ROUTE_MISMATCH"])
        return result("PASS", [])
    if not ready and not (receipt.expected_route == "HUMAN_ESCALATION" and escalated):
        # The report's reason string is externally supplied, so only bounded codes leave here.
        reason = report.summary.analysis_availability.reason_code
        safe_reason = (
            reason
            if reason
            in {
                "LEGAL_CORPUS_NOT_READY",
                "RISK_POLICY_UNAVAILABLE",
                "EVIDENCE_NOT_READY",
                "MISSING_CRITICAL_FACTS",
                "ANALYSIS_NOT_CONFIGURED",
            }
            else "ANALYSIS_UNAVAILABLE"
        )
        return result("UNAVAILABLE", [safe_reason])
    failures: list[str] = []
    if receipt.expected_route == "HUMAN_ESCALATION" and not escalated:
        failures.append("ROUTE_MISMATCH")
    if receipt.expected_route == "INTERNAL_DRAFT" and escalated:
        failures.append("ROUTE_MISMATCH")
    if report.risk is None or report.risk.level != receipt.expected_risk:
        failures.append("RISK_MISMATCH")
    if report.risk is None or report.risk.policy_version != receipt.expected_policy_version:
        failures.append("POLICY_MISMATCH")
    if report.risk is not None and report.risk.escalation_required != escalated:
        failures.append("ESCALATION_FLAG_MISMATCH")
    if ready:
        if report.analysis is None or report.analysis.as_of_date != receipt.as_of_date:
            failures.append("ANALYSIS_DATE_MISMATCH")
        if report.analysis is None or report.analysis.verifier_status != "PASSED":
            failures.append("VERIFIER_NOT_PASSED")
        source_ids = [source.fragment_id for source in report.legal_basis.sources]
        if len(set(source_ids)) != len(source_ids):
            failures.append("DUPLICATE_EVIDENCE")
        if not set(receipt.required_fragment_ids) <= set(source_ids):
            failures.append("REQUIRED_EVIDENCE_MISSING")
        for source in report.legal_basis.sources:
            if (
                receipt.as_of_date is None
                or source.effective_from > receipt.as_of_date
                or (source.effective_to is not None and receipt.as_of_date >= source.effective_to)
            ):
                failures.append("EVIDENCE_NOT_EFFECTIVE")
        if len(report.legal_conclusions) < receipt.required_legal_conclusion_count:
            failures.append("LEGAL_CONCLUSIONS_MISSING")
        if any(
            not set(conclusion.evidence_fragment_ids) <= set(source_ids)
            for conclusion in report.legal_conclusions
        ):
            failures.append("CONCLUSION_EVIDENCE_MISSING")
        if receipt.expected_route == "INTERNAL_DRAFT" and not report.recommendations.items:
            failures.append("INTERNAL_ACTIONS_MISSING")
    return result("FAIL" if failures else "PASS", failures)


def write_private_report(path: Path, report: ReferenceWorkbookAudit | ReferenceComparison) -> None:
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("PRIVATE_OUTPUT_DIRECTORY_REQUIRED")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(report.model_dump_json(by_alias=True, indent=2) + "\n")


def _read_private_json(path: Path) -> object:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or metadata.st_size > _MAX_JSON_BYTES
        ):
            raise ValueError("PRIVATE_INPUT_REQUIRED")
        return json.loads(stream.read(_MAX_JSON_BYTES + 1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit")
    audit.add_argument("workbook", type=Path)
    audit.add_argument("--review-manifest", type=Path)
    audit.add_argument("--output", required=True, type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("input", type=Path)
    compare.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "audit":
            manifest = (
                ReferenceReviewManifest.model_validate(_read_private_json(args.review_manifest))
                if args.review_manifest
                else None
            )
            report: ReferenceWorkbookAudit | ReferenceComparison = audit_workbook(
                args.workbook, manifest=manifest
            )
        else:
            request = ReferenceComparisonInput.model_validate(_read_private_json(args.input))
            report = compare_reference_report(request.reference, request.receipt, request.report)
        write_private_report(args.output, report)
    except (OSError, ValueError, ValidationError):
        # Never print schema errors or cell/parser values: those may include patient data.
        print("REFERENCE_INPUT_INVALID")
        return 1
    print("PRIVATE_REFERENCE_REPORT_WRITTEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
