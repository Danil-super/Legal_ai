from uuid import UUID

from telegram_gateway.clinic_document_library_runtime import render_library


VERSION_ID = UUID("00000000-0000-0000-0000-000000000111")


def test_empty_library_explains_upload_command() -> None:
    text, keyboard = render_library({"items": []})

    assert "/upload_clinic_doc" in text
    assert keyboard is None


def test_library_renders_metadata_without_document_content() -> None:
    payload = {
        "items": [
            {
                "id": "00000000-0000-0000-0000-000000000010",
                "documentKey": "warranty-main",
                "documentType": "WARRANTY_POLICY",
                "title": "Гарантийное положение",
                "versions": [
                    {
                        "id": str(VERSION_ID),
                        "versionNo": 3,
                        "sourceFilename": "warranty.pdf",
                        "reviewState": "APPROVED",
                        "rawSha256": "a" * 64,
                        "normalizedText": "SECRET DOCUMENT CONTENT",
                    }
                ],
            }
        ]
    }

    text, keyboard = render_library(payload)

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
