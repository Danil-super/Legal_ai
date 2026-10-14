"""Synthetic presentation checks: extracted PDF text, never legal validation."""

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from legal_core.contracts import CanonicalReport
from legal_core.reports import render_report_pdf
from telegram_gateway.analysis_runtime import telegram_analysis_summary
from telegram_gateway.case_wizard import telegram_summary_from_report


def presentation_report(*, level: str = "LOW", guided: bool = False) -> CanonicalReport:
    fragment_id = "00000000-0000-0000-0000-000000000030"
    return CanonicalReport.model_validate(
        {
            "reportId": "00000000-0000-0000-0000-000000000010",
            "reportVersion": 2,
            "generatedAt": datetime(2026, 10, 13, 10, 0, tzinfo=UTC),
            "case": {
                "id": "00000000-0000-0000-0000-000000000020",
                "publicNumber": "DL-2026-000001",
                "status": "REPORT_READY",
            },
            "summary": {
                "neutralDescription": "Синтетическое обращение о сколе конструкции.",
                "incidentTypes": [],
                "analysisAvailability": {"status": "READY", "reasonCode": None},
            },
            "facts": {
                "INTAKE_VERSION": "GUIDED_V2" if guided else "LEGACY_V1",
                "SERVICE_TYPE": "Синтетическая реставрация",
                "EVENT_DATE": {"date": "2026-10-12", "precision": "APPROXIMATE"},
                "PATIENT_DEMAND": ["REFUND_DEMAND"],
                "DEMAND_AMOUNT": {"amountKopecks": 123456, "currency": "RUB"},
                "CLINIC_ACTIONS": ["INVITED_FOR_EXAMINATION"] if guided else [],
                "CASE_MATERIALS_STATUS": "ATTACHED",
                "UNRECOGNIZED_INTERNAL_FIELD": "do-not-display",
            },
            "missingFacts": [],
            "recommendations": {"status": "AVAILABLE", "items": ["Синтетическая проверка."]},
            "draftResponse": (
                {
                    "status": "AVAILABLE",
                    "text": "Синтетический черновик <ответа>.",
                    "policyVersion": "safe-operational-draft.v1",
                }
                if level in {"LOW", "MEDIUM"}
                else {"status": "BLOCKED", "reasonCode": "HUMAN_LEGAL_REVIEW_REQUIRED"}
            ),
            "legalBasis": {
                "status": "AVAILABLE",
                "sources": [
                    {
                        "fragmentId": fragment_id,
                        "documentTitle": "Синтетический акт",
                        "officialNumber": "1",
                        "structuralPath": "article:10/part:2/point:3",
                        "effectiveFrom": "2026-10-01",
                        "effectiveTo": None,
                        "dateBasis": "LAWYER_CURRENT_COPY",
                        "extractionLimitations": ["Таблица не извлечена из синтетической копии."],
                        "sourceUrl": "https://publication.pravo.gov.ru/synthetic",
                        "textSha256": "a" * 64,
                        "rawSha256": "b" * 64,
                    }
                ],
            },
            "legalConclusions": [
                {
                    "claimId": "internal-claim-1",
                    "text": "Синтетический проверенный вывод.",
                    "evidenceFragmentIds": [fragment_id],
                }
            ],
            "risk": {
                "level": level,
                "reasonCodes": ["FORMAL_CLAIM_RECEIVED"]
                if level in {"HIGH", "CRITICAL"}
                else ["NO_ESCALATION_TRIGGER"],
                "policyVersion": "dental-risk.v4",
                "escalationRequired": level in {"HIGH", "CRITICAL"},
            },
            "analysis": {
                "analysisRunId": "00000000-0000-0000-0000-000000000011",
                "asOfDate": "2026-10-12",
                "verifierStatus": "PASSED",
                "evidenceTraceSha256": "c" * 64,
            },
            "factSnapshotSha256": "d" * 64,
            "disclaimer": "Внутренний отчёт. Пациенту автоматически не отправляется.",
        }
    )


def pdf_text(report: CanonicalReport, tmp_path: Path) -> str:
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(render_report_pdf(report))
    return subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True
    ).stdout


@pytest.mark.parametrize("level", ["LOW", "MEDIUM", "HIGH", "CRITICAL"])
@pytest.mark.parametrize("guided", [False, True])
def test_pdf_and_telegram_share_readable_blocks_without_internal_metadata(
    level: str,
    guided: bool,
    tmp_path: Path,
) -> None:
    report = presentation_report(level=level, guided=guided)
    for text in (
        pdf_text(report, tmp_path),
        telegram_summary_from_report(report.model_dump(mode="json", by_alias=True)),
        telegram_analysis_summary(
            {
                "analysisAllowed": True,
                "report": {"reportJson": report.model_dump(mode="json", by_alias=True)},
            }
        ),
    ):
        assert "План действий по обращению" in text
        for label in (
            "Ситуация и требования пациента",
            "Что уже сделала клиника",
            "Риск и следующий шаг",
            "План действий",
            "Проверенные выводы и нормативные основания",
            "Черновик ответа пациенту",
        ):
            assert label in text
        for internal in (
            "dental-risk.v4",
            "safe-operational-draft.v1",
            "SHA-256",
            "REPORT_READY",
            "LAWYER_CURRENT_COPY",
            "article:",
            "internal-claim-1",
            "do-not-display",
            "NO_ESCALATION_TRIGGER",
            "FORMAL_CLAIM_RECEIVED",
        ):
            assert internal not in text
        assert not re.search(r"00000000-0000-0000-0000-\d{12}", text)
        assert "1. Синтетическая проверка." in text
        assert "Синтетический проверенный вывод." in text
        assert "[1]" in text
        assert "статья 10" in text and "часть 2" in text and "пункт 3" in text
        assert "Дата редакции и публикации не установлена" in text
        assert "Таблица не извлечена" in text
        assert "12.10.2026" in text
        assert "1234,56" in text
        if guided:
            assert "пригласили на осмотр" in text
        if level in {"HIGH", "CRITICAL"}:
            assert "требуется юрист" in text
            assert "Получена письменная претензия" in text
            assert "Синтетический черновик" not in text
        else:
            assert "проверка человеком" in text
            assert "Синтетический черновик <ответа>." in text


def test_blocked_pdf_explains_missing_facts_without_promising_a_legal_plan(tmp_path: Path) -> None:
    payload = presentation_report().model_dump(mode="json", by_alias=True)
    payload.update(
        risk=None,
        analysis=None,
        legalConclusions=[],
        recommendations={"status": "NOT_AVAILABLE", "items": []},
        legalBasis={"status": "NOT_AVAILABLE", "sources": []},
        draftResponse={"status": "NOT_AVAILABLE", "text": None},
    )
    payload["summary"]["analysisAvailability"] = {
        "status": "BLOCKED",
        "reasonCode": "INSUFFICIENT_FACTS",
    }
    payload["missingFacts"] = [
        {
            "factKey": "SERVICE_DATE",
            "severity": "CRITICAL",
            "reasonCode": "DATE_MISSING",
            "questionId": "service_date",
        }
    ]
    report = CanonicalReport.model_validate(payload)
    for text in (
        pdf_text(report, tmp_path),
        telegram_summary_from_report(payload),
        telegram_analysis_summary(
            {
                "analysisAllowed": False,
                "riskLevel": "UNAVAILABLE",
                "report": {"reportJson": payload},
            }
        ),
    ):
        assert "Дата оказания услуги" in text
        assert "Уточните недостающие сведения" in text
        assert "Недостаточно сведений" in text
        assert "SERVICE_DATE" not in text and "INSUFFICIENT_FACTS" not in text
        assert "Критичные пробелы не выявлены" not in text
        assert "Синтетическая проверка" not in text
        assert "Синтетический акт" not in text


def test_long_pdf_preserves_end_of_every_section_and_determinism(tmp_path: Path) -> None:
    report = presentation_report()
    report.summary.neutral_description = (
        "Синтетический длинный текст <тег>. " * 150
    ) + "КОНЕЦ_ФАБУЛЫ"
    report.recommendations.items = ["Синтетическое действие. " * 150 + "КОНЕЦ_ДЕЙСТВИЯ"]
    report.draft_response.text = "Синтетический черновик. " * 150 + "КОНЕЦ_ЧЕРНОВИКА"
    text = pdf_text(report, tmp_path)
    assert "Синтетический длинный текст" in text.split("\f")[0]
    assert all(marker in text for marker in ("КОНЕЦ_ФАБУЛЫ", "КОНЕЦ_ДЕЙСТВИЯ", "КОНЕЦ_ЧЕРНОВИКА"))
    assert render_report_pdf(report) == render_report_pdf(report)
    telegram = telegram_summary_from_report(report.model_dump(mode="json", by_alias=True))
    assert len(telegram.encode("utf-16-le")) // 2 <= 4000
    assert "полностью в PDF" in telegram
    assert "Дата редакции и публикации не установлена" in telegram
    assert "Таблица не извлечена" in telegram


def test_pdf_preserves_saved_paragraph_breaks_and_escapes_external_markup(tmp_path: Path) -> None:
    report = presentation_report()
    report.summary.neutral_description = "Первая синтетическая строка.\nВторая строка <b>текст</b>."
    text = pdf_text(report, tmp_path)
    assert re.search(r"Первая синтетическая строка\.\n\s*Вторая строка <b>текст</b>\.", text)


def test_dated_source_keeps_extraction_limitations_without_current_copy_warning(
    tmp_path: Path,
) -> None:
    report = presentation_report()
    report.legal_basis.sources[0].date_basis = "DATED_EDITION"
    for text in (
        pdf_text(report, tmp_path),
        telegram_summary_from_report(report.model_dump(mode="json", by_alias=True)),
    ):
        assert "Таблица не извлечена" in text
        assert "Начало действия: 01.10.2026" in text
        assert "Дата редакции и публикации не установлена" not in text


def test_compact_telegram_does_not_drop_legal_conditions_or_source_citations() -> None:
    report = presentation_report()
    report.legal_conclusions[0].text = (
        "СИНТЕТИЧЕСКИЙ_ВЫВОД: "
        + "Условие применимости к этому вымышленному примеру. " * 55
        + "ОГОВОРКА: описанное действие не требуется."
    )
    report.recommendations.items = [
        "СИНТЕТИЧЕСКОЕ_ДЕЙСТВИЕ: "
        + "Синтетическое условие действия. " * 55
        + "ОГОВОРКА: требуется дополнительная проверка."
    ]
    text = telegram_summary_from_report(report.model_dump(mode="json", by_alias=True))
    assert "Проверенные выводы и нормативные основания полностью в PDF." in text
    assert "План действий полностью в PDF." in text
    assert "СИНТЕТИЧЕСКИЙ_ВЫВОД" not in text
    assert "СИНТЕТИЧЕСКОЕ_ДЕЙСТВИЕ" not in text
    assert "Дата редакции и публикации не установлена" in text
    assert "Таблица не извлечена" in text
    assert len(text.encode("utf-16-le")) // 2 <= 4000
