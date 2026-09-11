from telegram.ext import CallbackQueryHandler, CommandHandler
from telegram_gateway.legal_library_runtime import (
    _new_editor_state,
    build_application_with_legal_library,
    render_editor_version_detail,
    render_legal_library,
    render_platform_review_queue,
)


def _payload() -> dict[str, object]:
    title = "Правила предоставления платных медицинских услуг"
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


def test_platform_review_queue_renders_statuses_without_legal_text() -> None:
    text, keyboard = render_platform_review_queue(
        {
            "items": [
                {
                    "versionId": "00000000-0000-0000-0000-000000000002",
                    "documentTitle": "Правила платных медицинских услуг",
                    "officialNumber": "659",
                    "approvalState": "REVIEW_REQUIRED",
                    "artifactKind": "OFFICIAL_RAW",
                    "approvalEligible": True,
                    "effectiveFrom": "2026-09-01",
                    "effectiveTo": "2031-09-01",
                    "rawSha256": "a" * 64,
                    "fragmentCount": 11,
                    "normalizedText": "Raw text must stay hidden",
                }
            ]
        }
    )

    assert "ОЖИДАЕТ ПРОВЕРКИ" in text
    assert "Raw text must stay hidden" not in text
    assert keyboard.inline_keyboard[-1][0].callback_data == "menu"


def test_editor_detail_requires_all_four_explicit_attestations_before_confirm() -> None:
    detail = {
        "versionId": "00000000-0000-0000-0000-000000000002",
        "documentTitle": "Правила платных медицинских услуг",
        "issuer": "Правительство Российской Федерации",
        "officialNumber": "659",
        "sourceUrl": "https://example.test/official.pdf",
        "approvalState": "REVIEW_REQUIRED",
        "rawMimeType": "application/pdf",
        "rawSizeBytes": 100,
        "artifactPageCount": 1,
        "artifactRetrievedAt": "2026-09-09T12:00:00Z",
        "effectiveFrom": "2026-09-01",
        "effectiveTo": "2031-09-01",
        "rawSha256": "a" * 64,
        "normalizedSha256": "b" * 64,
        "fragmentsSha256": "c" * 64,
        "fragmentCount": 1,
        "approvalEligible": True,
    }
    state = _new_editor_state(detail)

    _, pending_keyboard = render_editor_version_detail(detail, state)
    pending_callbacks = {
        button.callback_data
        for row in pending_keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }
    assert "editor:confirm:00000000-0000-0000-0000-000000000002" not in pending_callbacks

    state["attestations"] = dict.fromkeys(
        ("source", "artifact", "dates", "fragments"), True
    )
    _, approved_keyboard = render_editor_version_detail(detail, state)
    approved_callbacks = {
        button.callback_data
        for row in approved_keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }
    assert "editor:confirm:00000000-0000-0000-0000-000000000002" in approved_callbacks


def test_editor_detail_opens_the_preserved_pdf_instead_of_fragment_screen() -> None:
    detail = {
        "versionId": "00000000-0000-0000-0000-000000000002",
        "documentTitle": "Правила платных медицинских услуг",
        "issuer": "Правительство Российской Федерации",
        "officialNumber": "659",
        "sourceUrl": "https://example.test/official.pdf",
        "approvalState": "REVIEW_REQUIRED",
        "rawMimeType": "application/pdf",
        "rawSizeBytes": 100,
        "artifactPageCount": 1,
        "artifactRetrievedAt": "2026-09-09T12:00:00Z",
        "effectiveFrom": "2026-09-01",
        "effectiveTo": "2031-09-01",
        "rawSha256": "a" * 64,
        "normalizedSha256": "b" * 64,
        "fragmentsSha256": "c" * 64,
        "fragmentCount": 1,
        "approvalEligible": True,
    }

    _, keyboard = render_editor_version_detail(detail, _new_editor_state(detail))
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }

    assert "📄 Открыть PDF" in labels
    assert "📑 Проверить фрагменты" not in labels
    assert "editor:artifact:00000000-0000-0000-0000-000000000002" in callbacks
    assert not any(callback.startswith("editor:fragments:") for callback in callbacks)


def test_editor_detail_labels_a_consultant_copy_without_calling_it_an_official_source() -> None:
    detail = {
        "versionId": "00000000-0000-0000-0000-000000000002",
        "documentTitle": "Федеральный закон",
        "issuer": "Российская Федерация",
        "officialNumber": "323-ФЗ",
        "sourceUrl": "https://www.consultant.ru/document/cons_doc_LAW_121895/",
        "approvalState": "REVIEW_REQUIRED",
        "artifactKind": "THIRD_PARTY_VERIFIED_COPY",
        "rawMimeType": "application/pdf",
        "rawSizeBytes": 100,
        "artifactPageCount": 1,
        "artifactRetrievedAt": "2026-09-11T12:00:00Z",
        "effectiveFrom": "2026-08-04",
        "effectiveTo": None,
        "rawSha256": "a" * 64,
        "normalizedSha256": "b" * 64,
        "fragmentsSha256": "c" * 64,
        "fragmentCount": 1,
        "approvalEligible": True,
    }

    text, keyboard = render_editor_version_detail(detail, _new_editor_state(detail))

    assert "КонсультантПлюс" in text
    assert "не первичная публикация" in text
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "🌐 Источник: КонсультантПлюс" in labels
    assert any("редакцией" in label for label in labels)


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
