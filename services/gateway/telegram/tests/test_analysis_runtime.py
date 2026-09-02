# ruff: noqa: RUF001
from uuid import UUID

import pytest
from telegram.ext import CallbackQueryHandler
from telegram_gateway.analysis_runtime import (
    analysis_keyboard,
    build_application_with_analysis,
    load_analysis_settings,
    telegram_analysis_messages,
    telegram_analysis_summary,
    telegram_lawyer_handoff_summary,
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
                        "policyVersion": "safe-operational-draft.v1",
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
    assert "Draft policy: safe-operational-draft.v1" in summary
    assert "Автоматическая отправка пациенту отключена" in summary


def test_high_risk_handoff_is_deidentified_and_has_only_verified_metadata() -> None:
    payload = {
        "analysisAllowed": True,
        "riskLevel": "HIGH",
        "escalationRequired": True,
        "clinicDocumentReadiness": [
            {
                "expectationCode": "CONTRACT",
                "importance": "CORE",
                "status": "NOT_AVAILABLE",
                "analysisBlocking": False,
            }
        ],
        "report": {
            "reportJson": {
                "case": {
                    "publicNumber": "DL-2026-000046",
                    "neutralDescription": "Пациент Иванов И. И. требует 90 000 рублей.",
                },
                "risk": {
                    "level": "HIGH",
                    "reasonCodes": ["FORMAL_CLAIM_RECEIVED"],
                    "escalationRequired": True,
                },
                "recommendations": {
                    "status": "AVAILABLE",
                    "items": ["Вернуть Иванову 90 000 рублей."],
                },
                "legalBasis": {
                    "status": "AVAILABLE",
                    "sources": [
                        {
                            "documentTitle": "Проверенный акт",
                            "structuralPath": "point:34",
                            "sourceUrl": "https://publication.pravo.gov.ru/example",
                        }
                    ]
                },
                "clinicDocuments": {
                    "status": "USED",
                    "sources": [{"normalizedText": "PRIVATE MEDICAL RECORD"}]
                },
                "draftResponse": {
                    "status": "AVAILABLE",
                    "text": "Здравствуйте, Иванов И. И.",
                },
            }
        },
    }

    handoff = telegram_lawyer_handoff_summary(payload)

    assert handoff is not None
    assert "DL-2026-000046" in handoff
    assert "FORMAL_CLAIM_RECEIVED" in handoff
    assert "договор на платные стоматологические услуги" in handoff
    assert "publication.pravo.gov.ru" in handoff
    assert "Иванов" not in handoff
    assert "90 000" not in handoff
    assert "PRIVATE MEDICAL RECORD" not in handoff
    assert "Вернуть" not in handoff
    assert "согласованный защищённый канал" in handoff
    assert telegram_analysis_messages(payload)[-1] == handoff


def test_low_risk_analysis_does_not_create_lawyer_handoff() -> None:
    payload = {
        "analysisAllowed": True,
        "escalationRequired": False,
        "clinicDocumentReadiness": [],
        "report": {
            "reportJson": {
                "case": {"publicNumber": "DL-2026-000047"},
                "risk": {"level": "LOW", "reasonCodes": [], "escalationRequired": False},
                "recommendations": {"items": []},
                "legalBasis": {"sources": []},
                "draftResponse": {"status": "BLOCKED", "reasonCode": "NOT_REQUIRED"},
            }
        },
    }

    assert telegram_lawyer_handoff_summary(payload) is None
    assert len(telegram_analysis_messages(payload)) == 1


def test_available_safe_patient_draft_is_shown_but_never_auto_sent() -> None:
    draft = (
        "Здравствуйте. Мы получили и зарегистрировали ваше обращение. "
        "До завершения проверки мы не будем делать выводы о причинах возникшей ситуации."
    )
    summary = telegram_analysis_summary(
        {
            "analysisAllowed": True,
            "riskLevel": "LOW",
            "escalationRequired": False,
            "report": {
                "reportJson": {
                    "case": {"publicNumber": "DL-2026-000043"},
                    "risk": {
                        "level": "LOW",
                        "reasonCodes": ["NO_ESCALATION_TRIGGER"],
                        "escalationRequired": False,
                    },
                    "recommendations": {
                        "status": "AVAILABLE",
                        "items": ["Предложить контрольный осмотр."],
                    },
                    "legalBasis": {"status": "AVAILABLE", "sources": []},
                    "draftResponse": {
                        "status": "AVAILABLE",
                        "text": draft,
                        "reasonCode": None,
                        "policyVersion": "safe-operational-draft.v1",
                    },
                }
            },
        }
    )

    assert "💬 Черновик ответа пациенту" in summary
    assert draft in summary
    assert "Draft policy: safe-operational-draft.v1" in summary
    assert "Перед отправкой текст должен проверить сотрудник клиники" in summary
    assert "Автоматическая отправка пациенту отключена" in summary


def test_clinic_document_provenance_is_separate_and_does_not_expose_text() -> None:
    summary = telegram_analysis_summary(
        {
            "analysisAllowed": True,
            "riskLevel": "LOW",
            "report": {
                "reportJson": {
                    "case": {"publicNumber": "DL-2026-000045"},
                    "risk": {
                        "level": "LOW",
                        "reasonCodes": [],
                        "escalationRequired": False,
                    },
                    "recommendations": {"items": ["Проверить документы клиники."]},
                    "legalBasis": {"sources": []},
                    "clinicDocuments": {
                        "status": "USED",
                        "sources": [
                            {
                                "documentTitle": "Гарантийное положение",
                                "documentType": "WARRANTY_POLICY",
                                "versionNo": 2,
                                "structuralPath": "section:3",
                                "normalizedText": "SECRET CLINIC DOCUMENT CONTENT",
                            }
                        ],
                    },
                    "draftResponse": {
                        "status": "AVAILABLE",
                        "text": "Здравствуйте. Обращение зарегистрировано.",
                        "policyVersion": "safe-operational-draft.v1",
                    },
                }
            },
        }
    )

    assert "📄 Документы клиники" in summary
    assert "Гарантийное положение, v2 · WARRANTY_POLICY · section:3" in summary
    assert "Не являются нормативной правовой основой" in summary
    assert "SECRET CLINIC DOCUMENT CONTENT" not in summary


def test_available_draft_without_text_is_rejected() -> None:
    with pytest.raises(ValueError, match="available patient draft has no text"):
        telegram_analysis_summary(
            {
                "analysisAllowed": True,
                "riskLevel": "LOW",
                "report": {
                    "reportJson": {
                        "case": {"publicNumber": "DL-2026-000044"},
                        "risk": {
                            "level": "LOW",
                            "reasonCodes": [],
                            "escalationRequired": False,
                        },
                        "recommendations": {"items": []},
                        "legalBasis": {"sources": []},
                        "draftResponse": {"status": "AVAILABLE", "text": None},
                    }
                },
            }
        )


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
