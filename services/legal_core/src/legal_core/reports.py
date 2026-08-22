"""Canonical intake report construction and deterministic PDF rendering."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import partial
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any
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
    CanonicalReport,
    CaseStatus,
    DraftResponse,
    FactKey,
    LegalBasis,
    MissingFact,
    Recommendations,
    ReportCase,
    ReportSummary,
)

DISCLAIMER = (
    "Внутренняя карточка. Не является окончательным юридическим заключением "
    "и не отправляется пациенту автоматически."
)
FONT_NAME = "DejaVuSans"
_FONT_PATHS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()


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
) -> CanonicalReport:
    serialized_facts = {key.value: value for key, value in sorted(facts.items())}
    facts_sha256 = hashlib.sha256(_canonical_json(serialized_facts)).hexdigest()
    incident_types = facts.get(FactKey.INCIDENT_TYPES, [])
    if not isinstance(incident_types, list):
        incident_types = []
    summary = facts.get(FactKey.PROBLEM_SUMMARY, "Описание ещё не заполнено.")
    if not isinstance(summary, str):
        summary = "Описание ещё не заполнено."

    return CanonicalReport(
        reportId=report_id,
        reportVersion=report_version,
        generatedAt=generated_at,
        case=ReportCase(id=case_id, publicNumber=public_number, status=case_status),
        summary=ReportSummary(
            neutralDescription=summary,
            incidentTypes=[str(value) for value in incident_types],
            analysisAvailability=AnalysisAvailability(),
        ),
        facts=serialized_facts,
        missingFacts=list(missing_facts),
        recommendations=Recommendations(),
        draftResponse=DraftResponse(),
        legalBasis=LegalBasis(),
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
        for item in report.missing_facts:
            story.append(Paragraph(f"• {escape(item.fact_key.value)}", body))
    else:
        story.append(Paragraph("Критичные пробелы не выявлены.", body))

    story.extend(
        [
            Paragraph("Юридический анализ", heading),
            Paragraph(
                "НЕ СФОРМИРОВАНО: LEGAL_CORPUS_NOT_READY. "
                "Рекомендации, оценка риска и черновик ответа пока недоступны.",
                body,
            ),
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
