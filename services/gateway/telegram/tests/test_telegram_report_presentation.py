"""Report copy uses synthetic saved fields and Telegram's UTF-16 message budget."""

import pytest
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


def _clarification_report() -> dict:
    report = _report()
    report["summary"]["analysisAvailability"]["reasonCode"] = "FACTUAL_CLARIFICATION_REQUIRED"
    report["facts"] = {
        "INTAKE_VERSION": "GUIDED_V2", "SITUATION_AREAS": ["TREATMENT"],
        "AFFECTED_SERVICES": ["Синтетическая услуга", "Синтетическая чистка"],
    }
    report["clarificationRequests"] = [
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
        {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 1},
        {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None},
    ]
    return report


def test_long_service_labels_preserve_all_three_questions_within_utf16_budget() -> None:
    report = _clarification_report()
    report["facts"]["AFFECTED_SERVICES"] = ["🦷" * 120, "🦷" * 120]
    report["summary"]["neutralDescription"] = "🦷" * 5000
    text = telegram_summary_from_report(report)
    assert len(text.encode("utf-16-le")) // 2 <= 4000
    assert text.count("Как оплачивалась услуга") == 1
    assert text.count("Когда была оказана услуга") == 1
    assert text.count("Что нужно сделать клинике по обращению?") == 1
    assert "Услуга 1" in text and "Услуга 2" in text
    assert "Ответ «неизвестно» допустим" in text


@pytest.mark.parametrize("question_id", ["SERVICE_PAYMENT_BASIS", "SERVICE_PROVIDED_DATE"])
def test_three_questions_keep_distinct_existing_slots_among_five_services(question_id: str) -> None:
    report = _clarification_report()
    report["facts"]["AFFECTED_SERVICES"] = ["🦷" * 120] * 5
    report["summary"]["neutralDescription"] = "🦷" * 5000
    report["clarificationRequests"] = [
        {"questionId": question_id, "serviceSlot": slot} for slot in (0, 2, 4)
    ]
    text = telegram_summary_from_report(report)
    assert len(text.encode("utf-16-le")) // 2 <= 4000
    assert all(f"Услуга {slot}" in text for slot in (1, 3, 5))
    assert "Ответ «неизвестно» допустим" in text
    assert text.count("оплачивалась" if question_id == "SERVICE_PAYMENT_BASIS" else "оказана") == 3


def test_explicit_unknown_answer_does_not_reappear_as_pending_request() -> None:
    report = _clarification_report()
    report["facts"]["ANALYSIS_CLARIFICATION_ANSWERS"] = {"answers": [
        {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0, "value": "UNKNOWN"},
    ]}
    text = telegram_summary_from_report(report)
    assert "Уточните факты:" not in text
    assert "Как оплачивалась" not in text


def test_blocked_questions_do_not_unlock_model_actions_sources_or_draft() -> None:
    report = _clarification_report()
    report["recommendations"] = {"status": "AVAILABLE", "items": ["UNVERIFIED_ADVICE"]}
    report["draftResponse"] = {"status": "AVAILABLE", "text": "UNVERIFIED_DRAFT"}
    report["legalBasis"] = {"status": "AVAILABLE", "sources": [
        {"documentTitle": "UNVERIFIED_SOURCE"},
    ]}
    report["legalConclusions"] = [{"text": "UNVERIFIED_CONCLUSION"}]
    text = telegram_summary_from_report(report)
    assert "Уточните факты:" in text
    assert "UNVERIFIED_" not in text
    assert "Подтверждённый юридический план в отчёте недоступен" in text


@pytest.mark.parametrize("invalid", [
    [{"questionId": "ARBITRARY", "serviceSlot": None}],
    [{"questionId": "RESPONSE_PURPOSE", "serviceSlot": 0}],
    [{"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 4}],
    [{"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": True}],
    [{"questionId": "RESPONSE_PURPOSE", "serviceSlot": None, "text": "INJECTED"}],
    [{"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0}] * 4,
])
def test_invalid_pending_question_batches_fail_closed(invalid: list[dict]) -> None:
    report = _clarification_report()
    report["clarificationRequests"] = invalid
    text = telegram_summary_from_report(report)
    assert "Уточните факты:" not in text
    assert "INJECTED" not in text and "ARBITRARY" not in text
    assert "Отсутствие списка пробелов" in text


@pytest.mark.parametrize("status", ["READY", "FUTURE_STATUS", None])
def test_only_blocked_reports_present_pending_questions(status: str | None) -> None:
    report = _clarification_report()
    report["summary"]["analysisAvailability"]["status"] = status
    assert "Уточните факты:" not in telegram_summary_from_report(report)
