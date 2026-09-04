# ruff: noqa: I001,RUF001
from telegram.ext import CallbackQueryHandler, CommandHandler
from telegram_gateway.legal_library_runtime import (
    build_application_with_legal_library,
    render_legal_library,
)


def _payload() -> dict[str, object]:
    title = (
        "Правила предоставления "
        "платных медицинских услуг"
    )
    return {
        "asOfDate": "2026-09-04",
        "items": [
            {
                "documentId": "00000000-0000-0000-0000-000000000001",
                "versionId": "00000000-0000-0000-0000-000000000002",
                "documentTitle": title,
                "issuer": "Правительство Российской Федерации",
                "officialNumber": "736",
                "effectiveFrom": "2023-09-01",
                "effectiveTo": "2026-09-01",
                "sourceUrl": "https://example.test/official.pdf",
                "rawSha256": "a" * 64,
                "fragmentCount": 7,
                "fragmentText": "This must never be rendered.",
            }
        ],
    }


def test_legal_library_renders_approved_metadata_and_official_link_only() -> None:
    text, keyboard = render_legal_library(_payload())

    assert "Правила предоставления" in text
    assert "Действует: 2023-09-01 — 2026-09-01" in text
    assert "aaaaaaaaaaaa…" in text
    assert "This must never be rendered." not in text
    assert keyboard.inline_keyboard[0][0].url == "https://example.test/official.pdf"
    assert keyboard.inline_keyboard[-1][0].callback_data == "menu"


def test_empty_legal_library_explains_that_legal_conclusions_stay_blocked() -> None:
    text, keyboard = render_legal_library({"asOfDate": "2026-09-04", "items": []})

    assert "нет одобренных источников" in text
    assert "заблокированными" in text
    assert keyboard.inline_keyboard[0][0].callback_data == "menu"


def test_composed_application_registers_lawyer_library_before_menu_handler(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("AGENT_INTERNAL_KEY", raising=False)
    application = build_application_with_legal_library("123456:unit_test_token_value_1234567890")
    handlers = application.handlers.get(-4, [])

    assert any(
        isinstance(handler, CallbackQueryHandler)
        and getattr(handler, "pattern", None) is not None
        and "legalbase" in str(handler.pattern.pattern)
        for handler in handlers
    )
    assert any(isinstance(handler, CommandHandler) for handler in handlers)
