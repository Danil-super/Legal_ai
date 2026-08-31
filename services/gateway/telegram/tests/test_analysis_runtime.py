from uuid import UUID

import pytest
from telegram.ext import CallbackQueryHandler
from telegram_gateway.analysis_runtime import (
    analysis_keyboard,
    build_application_with_analysis,
    load_analysis_settings,
    telegram_analysis_summary,
)


CASE_ID = UUID("00000000-0000-0000-0000-000000000010")


def test_analysis_settings_are_optional_when_completely_unconfigured() -> None:
    assert load_analysis_settings({}) is None


def test_analysis_settings_reject_partial_or_short_secret_configuration() -> None:
    with pytest.raises(RuntimeError, match="partially configured"):
        load_analysis_settings(
            {
                "AGENT_ORCHESTRATOR_URL": "http://agent-orchestrator:8010",
                "AGENT_INTERNAL_KEY": "short",
            }
        )


def test_analysis_keyboard_callback_fits_telegram_limit() -> None:
    keyboard = analysis_keyboard(CASE_ID)
    button = keyboard.inline_keyboard[0][0]

    assert button.callback_data == f"case:analyze:{CASE_ID}"
    assert len(button.callback_data.encode()) <= 64
    assert "юридический анализ" in button.text.lower()


def test_verified_analysis_summary_uses_only_canonical_server_report_fields() -> None:
    summary = telegram_analysis_summary(
        {
            "analysisAllowed": True,
            "riskLevel": "HIGH",
            "escalationRequired": True,
            "report": {
                "reportJson": {
                    "case": {"publicNumber": "DL-2026-000042"},
                    "risk": {
                        "level": "HIGH",
                        "reasonCodes": ["FORMAL_CLAIM_RECEIVED"],
                        "escalationRequired": True,
                    },
                    "recommendations": {
                        "status": "AVAILABLE",
                        "items": ["Передать кейс ответственному юристу."],
                    },
                    "legalBasis": {
                        "status": "AVAILABLE",
                        "sources": [
                            {
                                "documentTitle": "Проверенный акт",
                                "structuralPath": "point:34",
                                "sourceUrl": "https://publication.pravo.gov.ru/example",
                            }
                        ],
                    },
                    "draftResponse": {
                        "status": "BLOCKED",
                        "reasonCode": "HUMAN_LEGAL_REVIEW_REQUIRED",
                    },
                }
            },
        }
    )

    assert "DL-2026-000042" in summary
    assert "Риск: HIGH" in summary
    assert "Требуется передача" in summary
    assert "Передать кейс ответственному юристу" in summary
    assert "publication.pravo.gov.ru" in summary
    assert "HUMAN_LEGAL_REVIEW_REQUIRED" in summary
    assert "Автоматическая отправка пациенту отключена" in summary


def test_blocked_analysis_summary_refuses_to_invent_a_conclusion() -> None:
    summary = telegram_analysis_summary(
        {
            "analysisAllowed": False,
            "riskLevel": "UNAVAILABLE",
            "report": {},
        }
    )

    assert "не прошёл проверку доказательств" in summary
    assert "не будет додумывать вывод" in summary


def test_application_stays_legacy_compatible_without_analysis_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("AGENT_INTERNAL_KEY", raising=False)
    application = build_application_with_analysis("123456:unit_test_token_value_1234567890")

    handlers = [handler for group in application.handlers.values() for handler in group]
    analysis_handlers = [
        handler
        for handler in handlers
        if isinstance(handler, CallbackQueryHandler)
        and getattr(handler, "pattern", None) is not None
        and "case:analyze" in str(handler.pattern.pattern)
    ]
    assert analysis_handlers == []
