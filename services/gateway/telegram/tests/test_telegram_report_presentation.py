"""Report copy uses synthetic saved fields and Telegram's UTF-16 message budget."""

from telegram_gateway.case_wizard import telegram_summary_from_report


def _report() -> dict:
    return {
        "schemaVersion": "dental-case-report.v1",
        "generatedAt": "2026-10-13T10:00:00Z",
        "case": {"publicNumber": "DL-2026-000001", "status": "ANALYSIS_BLOCKED"},
        "summary": {
            "neutralDescription": "Синтетическая фабула <b>без форматирования</b>.",
            "analysisAvailability": {"status": "BLOCKED", "reasonCode": "FUTURE_BLOCK"},
        },
        "facts": {},
        "missingFacts": [],
        "recommendations": {"status": "NOT_AVAILABLE", "items": []},
        "draftResponse": {"status": "NOT_AVAILABLE"},
        "legalBasis": {"status": "NOT_AVAILABLE", "sources": []},
        "disclaimer": "Синтетический внутренний отчёт.",
    }


def test_unknown_blocker_and_empty_gaps_do_not_claim_enough_facts() -> None:
    text = telegram_summary_from_report(_report())
    assert "Причина недоступности анализа не распознана" in text
    assert "Отсутствие списка пробелов не подтверждает достаточность сведений" in text
    assert "FUTURE_BLOCK" not in text and "ANALYSIS_BLOCKED" not in text
    assert "<b>без форматирования</b>" in text  # The transport sends plain text.


def test_unknown_risk_does_not_become_low_risk_or_unlock_a_draft() -> None:
    report = _report()
    report["summary"]["analysisAvailability"] = {"status": "READY", "reasonCode": None}
    report["risk"] = {"level": "FUTURE_LEVEL", "reasonCodes": ["FUTURE_REASON"]}
    report["draftResponse"] = {"status": "AVAILABLE", "text": "НЕЛЬЗЯ_ПОКАЗАТЬ"}
    text = telegram_summary_from_report(report)
    assert "Уровень риска неизвестен" in text
    assert "Сохранённая причина риска не распознана" in text
    assert "Низкий риск" not in text and "НЕЛЬЗЯ_ПОКАЗАТЬ" not in text
    assert "FUTURE_LEVEL" not in text and "FUTURE_REASON" not in text


def test_unknown_analysis_status_keeps_actions_sources_and_draft_unavailable() -> None:
    report = _report()
    report["summary"]["analysisAvailability"]["status"] = "FUTURE_STATUS"
    report["recommendations"] = {"status": "AVAILABLE", "items": ["НЕЛЬЗЯ_ПОКАЗАТЬ"]}
    report["draftResponse"] = {"status": "AVAILABLE", "text": "НЕЛЬЗЯ_ПОКАЗАТЬ"}
    text = telegram_summary_from_report(report)
    assert "Статус анализа неизвестен" in text
    assert "НЕЛЬЗЯ_ПОКАЗАТЬ" not in text and "FUTURE_STATUS" not in text


def test_legacy_generic_check_is_not_presented_as_detailed_legal_actions() -> None:
    report = _report()
    report["summary"]["analysisAvailability"] = {"status": "READY", "reasonCode": None}
    report["risk"] = {"level": "LOW", "reasonCodes": []}
    report["recommendations"] = {
        "status": "AVAILABLE",
        "items": ["Передайте карточку ответственному сотруднику для внутренней проверки."],
    }
    text = telegram_summary_from_report(report)
    assert "Сохранена только общая внутренняя проверка" in text
    assert "подробные юридические действия не сохранены" in text


def test_long_astral_text_keeps_lawyer_handoff_and_unavailable_draft_within_budget() -> None:
    report = _report()
    report["summary"]["neutralDescription"] = "🦷" * 5000
    report["summary"]["analysisAvailability"] = {"status": "READY", "reasonCode": None}
    report["risk"] = {
        "level": "HIGH",
        "reasonCodes": ["FUTURE_REASON"] * 30,
        "escalationRequired": True,
    }
    report["draftResponse"] = {"status": "BLOCKED", "reasonCode": "HUMAN_LEGAL_REVIEW_REQUIRED"}
    report["missingFacts"] = [{"factKey": "SERVICE_DATE"}] * 30
    text = telegram_summary_from_report(report)
    assert len(text.encode("utf-16-le")) // 2 <= 4000
    assert "передайте кейс юристу" in text
    assert "Для подготовки ответа требуется проверка юристом" in text
    assert "полностью в PDF" in text
