"""Canonical intake/analysis report construction and deterministic PDF rendering."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from functools import partial
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.enums import TA_CENTER  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]
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
    DraftResponse,
    FactKey,
    LegalBasis,
    LegalSourceCard,
    MissingFact,
    Recommendations,
    ReportCase,
    ReportSummary,
    RiskSummary,
)
from legal_core.legal_retrieval import ApprovedLegalFragment
from legal_core.risk_engine import RiskAssessment, RiskLevel

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
        factSnapshotSha256=facts_sha256,
        disclaimer=DISCLAIMER,
    )


def _source_cards(evidence: Sequence[ApprovedLegalFragment]) -> list[LegalSourceCard]:
    unique: dict[UUID, ApprovedLegalFragment] = {}
    for fragment in evidence:
        unique.setdefault(fragment.fragment_id, fragment)
    return [
        LegalSourceCard(
            fragmentId=fragment.fragment_id,
            documentTitle=fragment.document_title,
            officialNumber=fragment.official_number,
            structuralPath=fragment.structural_path,
            effectiveFrom=fragment.effective_from,
            effectiveTo=fragment.effective_to,
            sourceUrl=fragment.source_url,
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
    verified_action_items: Sequence[str],
) -> CanonicalReport:
    """Build a user-visible report only after all server-side evidence gates passed."""

    if risk.level is RiskLevel.UNAVAILABLE:
        raise ValueError("an unavailable risk result cannot produce a READY analysis report")
    if not evidence:
        raise ValueError("a READY analysis report requires approved legal evidence")

    serialized_facts, facts_sha256 = _serialized_facts(facts)
    incident_types, summary = _summary_parts(facts)
    actions = [item.strip() for item in verified_action_items if item.strip()]
    recommendation = (
        Recommendations(status="AVAILABLE", items=actions)
        if actions
        else Recommendations(
            status="AVAILABLE",
            items=[
                "Передайте карточку ответственному сотруднику для внутренней проверки."
            ],
        )
    )
    escalation_required = risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    draft_reason = (
        "HUMAN_LEGAL_REVIEW_REQUIRED"
        if escalation_required
        else "DRAFT_VERIFIER_NOT_ENABLED"
    )
    risk_level = cast(RiskLevelLiteral, risk.level.value)

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
        draftResponse=DraftResponse(status="BLOCKED", reasonCode=draft_reason),
        legalBasis=LegalBasis(status="AVAILABLE", sources=_source_cards(evidence)),
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

        story.append(Paragraph("Рекомендованные действия", heading))
        for recommendation_item in report.recommendations.items:
            story.append(Paragraph(f"• {escape(recommendation_item)}", body))

        story.append(Paragraph("Черновик ответа пациенту", heading))
        if report.draft_response.status == "AVAILABLE" and report.draft_response.text:
            story.append(Paragraph(escape(report.draft_response.text), body))
        else:
            reason = report.draft_response.reason_code or "NOT_AVAILABLE"
            story.append(Paragraph(f"Не сформирован: {escape(reason)}.", body))

        story.append(Paragraph("Правовая основа", heading))
        for source in report.legal_basis.sources:
            number = f" № {escape(source.official_number)}" if source.official_number else ""
            story.append(
                Paragraph(
                    f"• {escape(source.document_title)}{number}; "
                    f"{escape(source.structural_path)}; действует с "
                    f"{source.effective_from.isoformat()}.<br/>"
                    f"Источник: {escape(source.source_url)}",
                    body,
                )
            )
        story.append(
            Paragraph(
                f"Evidence trace SHA-256: {report.analysis.evidence_trace_sha256}",
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
    deterministic_canvas = partial(canvas.Canvas, invariant=1)
    document.build(story, canvasmaker=deterministic_canvas)
    rendered = output.getvalue()
    # ReportLab 5 still varies only the trailer document ID between identical renders.
    # Replacing the fixed-width ID with the canonical report digest keeps the file byte-stable
    # without changing offsets or rendered content.
    document_id = hashlib.sha256(
        _canonical_json(report.model_dump(mode="json", by_alias=True))
    ).hexdigest()[:32].encode()
    return re.sub(
        rb"(/ID\s*\[<)[0-9a-f]{32}(><)[0-9a-f]{32}(>\])",
        lambda match: (
            match.group(1)
            + document_id
            + match.group(2)
            + document_id
            + match.group(3)
        ),
        rendered,
        count=1,
    )
