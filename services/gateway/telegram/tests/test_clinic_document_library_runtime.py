# ruff: noqa: I001
from uuid import UUID

from telegram_gateway.clinic_document_library_runtime import (
    render_document_history,
    render_library,
)


VERSION_ID = UUID("00000000-0000-0000-0000-000000000111")
DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000010")


def _payload() -> dict[str, object]:
    return {
        "items": [
            {
                "id": str(DOCUMENT_ID),
                "documentKey": "warranty-main",
                "documentType": "WARRANTY_POLICY",
                "title": "Гарантийное положение",
                "versions": [
                    {
                        "id": str(VERSION_ID),
                        "versionNo": 3,
                        "sourceFilename": "warranty.pdf",
                        "reviewState": "APPROVED",
                        "reviewReasonCode": "CLINIC_REVIEW_PASSED",
                        "rawSha256": "a" * 64,
                        "validFrom": "2026-01-01",
                        "validTo": None,
                        "normalizedText": "SECRET DOCUMENT CONTENT",
                    },
                    {
                        "id": "00000000-0000-0000-0000-000000000110",
                        "versionNo": 2,
                        "sourceFilename": "warranty-old.pdf",
                        "reviewState": "BLOCKED",
                        "reviewReasonCode": "CLINIC_DOCUMENT_REVOKED",
                        "rawSha256": "b" * 64,
                        "validFrom": "2025-01-01",
                        "validTo": "2026-01-01",
                    },
                ],
            }
        ]
    }


def test_empty_library_explains_upload_command() -> None:
    text, keyboard = render_library({"items": []})

    assert "/upload_clinic_doc" in text
    assert keyboard is None


def test_library_renders_metadata_without_document_content() -> None:
    text, keyboard = render_library(_payload())

    assert "Гарантийное положение" in text
    assert "warranty-main" in text
    assert "v3" in text
    assert "APPROVED" in text
    assert "aaaaaaaaaaaa…" in text
    assert "SECRET DOCUMENT CONTENT" not in text
    assert keyboard is not None
    callback_values = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]
    assert f"clinicdoc:approve:{VERSION_ID}" in callback_values
    assert f"clinicdoc:block:{VERSION_ID}" in callback_values
    assert f"cliniclib:history:{DOCUMENT_ID}" in callback_values


def test_document_history_shows_reviewed_versions_without_contents() -> None:
    text = render_document_history(_payload(), DOCUMENT_ID)

    assert "История: Гарантийное положение" in text
    assert "v3 · APPROVED" in text
    assert "v2 · BLOCKED" in text
    assert "2025-01-01 → 2026-01-01" in text
    assert "CLINIC_DOCUMENT_REVOKED" in text
    assert "SECRET DOCUMENT CONTENT" not in text


def test_library_output_is_bounded() -> None:
    items = []
    for index in range(30):
        items.append(
            {
                "id": f"00000000-0000-0000-0000-{index:012d}",
                "documentKey": f"document-{index}",
                "documentType": "CONTRACT",
                "title": "Очень длинное название " * 20,
                "versions": [],
            }
        )

    text, _ = render_library({"items": items})

    assert len(text) <= 3900
    assert "…и ещё 10 документов." in text or "…список сокращён." in text
