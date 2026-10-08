"""Canonical intake/analysis report construction and deterministic PDF rendering."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from html import escape
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.enums import TA_CENTER  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from legal_core.contracts import (
    AnalysisAvailability,
    AnalysisSnapshot,
    CanonicalReport,
    CaseStatus,
    ClinicDocumentBasis,
    ClinicDocumentSourceCard,
    DraftResponse,
    FactKey,
    LegalBasis,
    LegalConclusion,
    LegalSourceCard,
    MissingFact,
    Recommendations,
    ReportCase,
    ReportSummary,
    RiskSummary,
)
from legal_core.risk_engine import RiskAssessment, RiskLevel
from legal_core.safe_patient_draft import build_safe_patient_draft
from legal_core.verifier import VerificationDecision, VerificationResult

if TYPE_CHECKING:
    from legal_core.clinic_document_retrieval import ApprovedClinicDocumentFragment
    from legal_core.legal_retrieval import ApprovedLegalFragment

DISCLAIMER = (
    "Внутренняя карточка. Не является окончательным юридическим заключением "
    "и не отправляется пациенту автоматически."
)
FONT_NAME = "DejaVuSans"
_FONT_PATHS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
)
RiskLevelLiteral = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNAVAILABLE"]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()


def _serialized_facts(facts: Mapping[FactKey, object]) -> tuple[dict[str, object], str]:
    serialized = {key.value: value for key, value in sorted(facts.items())}
    return serialized, hashlib.sha256(_canonical_json(serialized)).hexdigest()


def _summary_parts(facts: Mapping[FactKey, object]) -> tuple[list[str], str]:
    if facts.get(FactKey.INTAKE_VERSION) == "GUIDED_V2":
        areas = facts.get(FactKey.SITUATION_AREAS, [])
        event_summary = facts.get(FactKey.EVENT_SUMMARY, "Описание ещё не заполнено.")
        return (
            [str(value) for value in areas] if isinstance(areas, list) else [],
            event_summary if isinstance(event_summary, str) else "Описание ещё не заполнено.",
        )
    incident_types = facts.get(FactKey.INCIDENT_TYPES, [])
    if not isinstance(incident_types, list):
        incident_types = []
    summary = facts.get(FactKey.PROBLEM_SUMMARY, "Описание ещё не заполнено.")
    if not isinstance(summary, str):
        summary = "Описание ещё не заполнено."
    return [str(value) for value in incident_types], summary


def build_intake_report(
    *,
    report_id: UUID,
    case_id: UUID,
    public_number: str,
    case_status: CaseStatus,
    report_version: int,
    generated_at: datetime,
    facts: Mapping[FactKey, object],
    missing_facts: Sequence[MissingFact],
    block_reason_code: str = "LEGAL_CORPUS_NOT_READY",
) -> CanonicalReport:
    serialized_facts, facts_sha256 = _serialized_facts(facts)
    incident_types, summary = _summary_parts(facts)

    return CanonicalReport(
        reportId=report_id,
        reportVersion=report_version,
        generatedAt=generated_at,
        case=ReportCase(id=case_id, publicNumber=public_number, status=case_status),
        summary=ReportSummary(
            neutralDescription=summary,
            incidentTypes=incident_types,
            analysisAvailability=AnalysisAvailability(
                status="BLOCKED",
                reasonCode=block_reason_code,
            ),
        ),
        facts=serialized_facts,
        missingFacts=list(missing_facts),
        recommendations=Recommendations(),
        draftResponse=DraftResponse(),
        legalBasis=LegalBasis(),
        clinicDocuments=ClinicDocumentBasis(),
        factSnapshotSha256=facts_sha256,
        disclaimer=DISCLAIMER,
    )


def _source_cards(
    evidence: Sequence[ApprovedLegalFragment],
    verification: VerificationDecision,
    *,
    as_of_date: date,
) -> list[LegalSourceCard]:
    if not verification.analysis_allowed:
        raise ValueError("READY report requires fully verified claims")
    evidence_by_id = {fragment.fragment_id: fragment for fragment in evidence}
    if len(evidence_by_id) != len(evidence):
        raise ValueError("retrieved evidence fragment identifiers must be unique")
    cited_ids: set[UUID] = set()
    claim_ids: set[str] = set()
    for claim in verification.claims:
        if claim.result is not VerificationResult.VERIFIED or not claim.verified_fragment_ids:
            raise ValueError("READY report requires cited verified claims")
        if claim.claim_id in claim_ids or len(claim.verified_fragment_ids) != len(
            set(claim.verified_fragment_ids)
        ):
            raise ValueError("verified claim identifiers and citations must be unique")
        claim_ids.add(claim.claim_id)
        cited_ids.update(claim.verified_fragment_ids)
    if not cited_ids or not cited_ids.issubset(evidence_by_id):
        raise ValueError("verified citation is missing from retrieved evidence")
    for fragment_id in cited_ids:
        fragment = evidence_by_id[fragment_id]
        if fragment.effective_from > as_of_date or (
            fragment.effective_to is not None and as_of_date >= fragment.effective_to
        ):
            raise ValueError("verified citation is not effective on the case date")
    return [
        LegalSourceCard(
            fragmentId=fragment.fragment_id,
            documentTitle=fragment.document_title,
            officialNumber=fragment.official_number,
            structuralPath=fragment.structural_path,
            effectiveFrom=fragment.effective_from,
            effectiveTo=fragment.effective_to,
            dateBasis=fragment.date_basis,
            extractionLimitations=fragment.extraction_limitations,
            sourceUrl=fragment.source_url,
            textSha256=fragment.text_sha256,
            rawSha256=fragment.raw_sha256,
        )
        for fragment in evidence
        if fragment.fragment_id in cited_ids
    ]


def _clinic_document_cards(
    context: Sequence[ApprovedClinicDocumentFragment],
) -> list[ClinicDocumentSourceCard]:
    unique: dict[UUID, ApprovedClinicDocumentFragment] = {}
    for fragment in context:
        unique.setdefault(fragment.fragment_id, fragment)
    return [
        ClinicDocumentSourceCard(
            fragmentId=fragment.fragment_id,
            versionId=fragment.version_id,
            documentId=fragment.document_id,
            documentKey=fragment.document_key,
            documentType=fragment.document_type,
            documentTitle=fragment.document_title,
            versionNo=fragment.version_no,
            validFrom=fragment.valid_from,
            validTo=fragment.valid_to,
            structuralPath=fragment.structural_path,
            textSha256=fragment.text_sha256,
            rawSha256=fragment.raw_sha256,
        )
        for fragment in unique.values()
    ]


def build_analysis_report(
    *,
    report_id: UUID,
    analysis_run_id: UUID,
    case_id: UUID,
    public_number: str,
    case_status: CaseStatus,
    report_version: int,
    generated_at: datetime,
    as_of_date: date,
    facts: Mapping[FactKey, object],
    missing_facts: Sequence[MissingFact],
    risk: RiskAssessment,
    evidence_trace_sha256: str,
    evidence: Sequence[ApprovedLegalFragment],
    verification: VerificationDecision,
    clinic_document_context_trace_sha256: str,
    clinic_document_context: Sequence[ApprovedClinicDocumentFragment],
    verified_action_items: Sequence[str],
    verified_legal_conclusions: Sequence[LegalConclusion] = (),
) -> CanonicalReport:
    """Build a user-visible report only after all server-side evidence gates passed."""

    if risk.level is RiskLevel.UNAVAILABLE:
        raise ValueError("an unavailable risk result cannot produce a READY analysis report")
    if not evidence:
        raise ValueError("a READY analysis report requires approved legal evidence")
    verified_by_claim = {claim.claim_id: claim for claim in verification.claims}
    for conclusion in verified_legal_conclusions:
        verified = verified_by_claim.get(conclusion.claim_id)
        if (
            verified is None
            or tuple(conclusion.evidence_fragment_ids) != verified.verified_fragment_ids
        ):
            raise ValueError("legal conclusion requires a matching verified claim and citations")

    serialized_facts, facts_sha256 = _serialized_facts(facts)
    incident_types, summary = _summary_parts(facts)
    actions = [item.strip() for item in verified_action_items if item.strip()]
    recommendation = (
        Recommendations(status="AVAILABLE", items=actions)
        if actions
        else Recommendations(
            status="AVAILABLE",
            items=["Передайте карточку ответственному сотруднику для внутренней проверки."],
        )
    )
    escalation_required = risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    draft_response = build_safe_patient_draft(facts, risk)
    risk_level = cast(RiskLevelLiteral, risk.level.value)
    clinic_cards = _clinic_document_cards(clinic_document_context)
    clinic_basis = (
        ClinicDocumentBasis(status="USED", sources=clinic_cards)
        if clinic_cards
        else ClinicDocumentBasis()
    )

    return CanonicalReport(
        reportId=report_id,
        reportVersion=report_version,
        generatedAt=generated_at,
        case=ReportCase(id=case_id, publicNumber=public_number, status=case_status),
        summary=ReportSummary(
            neutralDescription=summary,
            incidentTypes=incident_types,
            analysisAvailability=AnalysisAvailability(status="READY", reasonCode=None),
        ),
        facts=serialized_facts,
        missingFacts=list(missing_facts),
        recommendations=recommendation,
        draftResponse=draft_response,
        legalBasis=LegalBasis(
            status="AVAILABLE",
            sources=_source_cards(evidence, verification, as_of_date=as_of_date),
        ),
        legalConclusions=list(verified_legal_conclusions),
        clinicDocuments=clinic_basis,
        risk=RiskSummary(
            level=risk_level,
            reasonCodes=list(risk.reason_codes),
            policyVersion=risk.policy_version,
            escalationRequired=escalation_required,
        ),
        analysis=AnalysisSnapshot(
            analysisRunId=analysis_run_id,
            asOfDate=as_of_date,
            verifierStatus="PASSED",
            evidenceTraceSha256=evidence_trace_sha256,
            clinicDocumentContextTraceSha256=clinic_document_context_trace_sha256,
        ),
        factSnapshotSha256=facts_sha256,
        disclaimer=DISCLAIMER,
    )


def _register_font() -> None:
    if FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return
    for path in _FONT_PATHS:
        if path.is_file():
            pdfmetrics.registerFont(TTFont(FONT_NAME, str(path)))
            return
    raise RuntimeError("DejaVu Sans font is required for Cyrillic PDF reports")


def render_report_pdf(report: CanonicalReport) -> bytes:
    """Render a byte-stable PDF from the validated canonical report only."""

    _register_font()
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Dental Legal AI — {report.case.public_number}",
        author="Dental Legal AI",
        invariant=1,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName=FONT_NAME,
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#173F5F"),
        alignment=TA_CENTER,
        spaceAfter=8 * mm,
    )
    heading = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontName=FONT_NAME,
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#173F5F"),
        spaceBefore=5 * mm,
        spaceAfter=2 * mm,
    )
    body = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName=FONT_NAME,
        fontSize=9.5,
        leading=13,
    )
    muted = ParagraphStyle(
        "ReportMuted",
        parent=body,
        textColor=colors.HexColor("#5F6B76"),
        fontSize=8,
    )

    story: list[Any] = [
        Paragraph("DENTAL LEGAL AI", title),
        Paragraph(f"Карточка кейса {escape(report.case.public_number)}", heading),
        Table(
            [
                ["Версия", str(report.report_version)],
                ["Статус", report.case.status.value],
                ["Сформировано", report.generated_at.isoformat()],
            ],
            colWidths=[42 * mm, 118 * mm],
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAF3F7")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B8CBD5")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 5),
                ]
            ),
        ),
        Paragraph("Что произошло", heading),
        Paragraph(escape(report.summary.neutral_description), body),
        Paragraph("Недостающая информация", heading),
    ]
    if report.missing_facts:
        for missing_item in report.missing_facts:
            story.append(Paragraph(f"• {escape(missing_item.fact_key.value)}", body))
    else:
        story.append(Paragraph("Критичные пробелы не выявлены.", body))

    if report.summary.analysis_availability.status == "READY":
        if report.risk is None or report.analysis is None:  # pragma: no cover - contract guards it.
            raise ValueError("READY report is missing analysis snapshots")
        story.extend(
            [
                Paragraph("Юридический анализ", heading),
                Paragraph(
                    f"Уровень риска: <b>{escape(report.risk.level)}</b>",
                    body,
                ),
            ]
        )
        for reason in report.risk.reason_codes:
            story.append(Paragraph(f"• {escape(reason)}", body))

        story.append(Paragraph("Юридическая оценка", heading))
        if report.legal_conclusions:
            source_numbers = {
                source.fragment_id: index
                for index, source in enumerate(report.legal_basis.sources, start=1)
            }
            for index, conclusion in enumerate(report.legal_conclusions, start=1):
                story.append(Paragraph(f"{index}. {escape(conclusion.text)}", body))
                citations = ", ".join(
                    f"[{source_numbers[fragment_id]}]"
                    for fragment_id in conclusion.evidence_fragment_ids
                )
                story.append(Paragraph(f"Основание: {citations}.", muted))
        else:
            story.append(
                Paragraph(
                    "Отдельные проверенные юридические выводы в этом отчёте не сохранены.", body
                )
            )

        story.append(Paragraph("Рекомендованные действия", heading))
        for recommendation_item in report.recommendations.items:
            story.append(Paragraph(f"• {escape(recommendation_item)}", body))

        story.append(Paragraph("Черновик ответа пациенту", heading))
        if report.draft_response.status == "AVAILABLE" and report.draft_response.text:
            story.append(Paragraph(escape(report.draft_response.text), body))
        else:
            reason = report.draft_response.reason_code or "NOT_AVAILABLE"
            story.append(Paragraph(f"Не сформирован: {escape(reason)}.", body))
        if report.draft_response.policy_version:
            story.append(
                Paragraph(
                    f"Draft policy: {escape(report.draft_response.policy_version)}",
                    muted,
                )
            )

        story.append(Paragraph("Правовая основа", heading))
        for source_index, legal_source in enumerate(report.legal_basis.sources, start=1):
            applicability_label = (
                "проверенная применимость начиная с даты"
                if legal_source.date_basis == "LAWYER_CURRENT_COPY"
                else "действует с"
            )
            number = (
                f" № {escape(legal_source.official_number)}" if legal_source.official_number else ""
            )
            story.append(
                Paragraph(
                    f"[{source_index}] {escape(legal_source.document_title)}{number}; "
                    f"{escape(legal_source.structural_path)}; {applicability_label} "
                    f"{legal_source.effective_from.isoformat()}.<br/>"
                    f"Источник: {escape(legal_source.source_url)}",
                    body,
                )
            )
            if legal_source.date_basis == "LAWYER_CURRENT_COPY":
                story.append(
                    Paragraph(
                        "Дата редакции и публикации не установлена; более ранняя "
                        "применимость этой копии не подтверждена.",
                        muted,
                    )
                )
                for limitation in legal_source.extraction_limitations:
                    story.append(Paragraph(escape(limitation), muted))

        if report.clinic_documents.status == "USED":
            story.append(Paragraph("Документы клиники — внутренний контекст", heading))
            story.append(
                Paragraph(
                    "Эти документы не являются нормативной правовой основой и не заменяют закон.",
                    muted,
                )
            )
            for clinic_source in report.clinic_documents.sources:
                period = ""
                if clinic_source.valid_from is not None:
                    period = f"; действует с {clinic_source.valid_from.isoformat()}"
                if clinic_source.valid_to is not None:
                    period += f" до {clinic_source.valid_to.isoformat()}"
                story.append(
                    Paragraph(
                        f"• {escape(clinic_source.document_title)}; v{clinic_source.version_no}; "
                        f"{escape(clinic_source.document_type)}; "
                        f"{escape(clinic_source.structural_path)}{period}.",
                        body,
                    )
                )

        story.append(
            Paragraph(
                f"Evidence trace SHA-256: {report.analysis.evidence_trace_sha256}",
                muted,
            )
        )
        if report.analysis.clinic_document_context_trace_sha256:
            story.append(
                Paragraph(
                    "Clinic context trace SHA-256: "
                    f"{report.analysis.clinic_document_context_trace_sha256}",
                    muted,
                )
            )
    else:
        reason = report.summary.analysis_availability.reason_code or "ANALYSIS_BLOCKED"
        story.extend(
            [
                Paragraph("Юридический анализ", heading),
                Paragraph(
                    f"НЕ СФОРМИРОВАНО: {escape(reason)}. "
                    "Рекомендации, оценка риска и черновик ответа недоступны.",
                    body,
                ),
            ]
        )

    story.extend(
        [
            Spacer(1, 8 * mm),
            Paragraph(escape(report.disclaimer), muted),
            Paragraph(
                f"Fact snapshot SHA-256: {report.fact_snapshot_sha256}",
                muted,
            ),
        ]
    )
    document.build(story)
    rendered = output.getvalue()
    # ReportLab 5 still varies only the trailer document ID between identical renders.
    # Replacing the fixed-width ID with the canonical report digest keeps the file byte-stable
    # without changing offsets or rendered content.
    document_id = (
        hashlib.sha256(_canonical_json(report.model_dump(mode="json", by_alias=True)))
        .hexdigest()[:32]
        .encode()
    )
    return re.sub(
        rb"(/ID\s*\[<)[0-9A-Fa-f]{32}(><)[0-9A-Fa-f]{32}(>\])",
        lambda match: match.group(1) + document_id + match.group(2) + document_id + match.group(3),
        rendered,
        count=1,
    )
