from uuid import UUID

import pytest
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler
from telegram_gateway.clinic_document_runtime import (
    build_application_with_clinic_documents,
    parse_upload_command,
    review_keyboard,
)

VERSION_ID = UUID("00000000-0000-0000-0000-000000000123")


def test_upload_command_is_explicit_and_normalizes_only_key_and_type() -> None:
    pending = parse_upload_command(
        [
            "Warranty-Main",
            "warranty_policy",
            "Гарантийное",
            "положение",
        ]
    )

    assert pending.document_key == "warranty-main"
    assert pending.document_type == "WARRANTY_POLICY"
    assert pending.title == "Гарантийное положение"


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["x", "TYPE", "Название"],
        ["bad/key", "TYPE", "Название"],
        ["valid-key", "bad-type", "Название"],
        ["valid-key", "VALID_TYPE", ""],
    ],
)
def test_upload_command_rejects_ambiguous_or_invalid_metadata(arguments: list[str]) -> None:
    with pytest.raises(ValueError):
        parse_upload_command(arguments)


def test_review_keyboard_uses_bounded_explicit_approval_callbacks() -> None:
    keyboard = review_keyboard(VERSION_ID)
    approve, block = keyboard.inline_keyboard[0]

    assert approve.callback_data == f"clinicdoc:approve:{VERSION_ID}"
    assert block.callback_data == f"clinicdoc:block:{VERSION_ID}"
    assert len(approve.callback_data.encode()) <= 64
    assert len(block.callback_data.encode()) <= 64


def test_application_registers_upload_and_review_before_generic_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("AGENT_INTERNAL_KEY", raising=False)
    application = build_application_with_clinic_documents(
        "123456:unit_test_token_value_1234567890"
    )

    early_handlers = application.handlers.get(-2, [])
    commands = {
        handler.commands
        for handler in early_handlers
        if isinstance(handler, CommandHandler)
    }
    assert frozenset({"upload_clinic_doc"}) in commands
    assert frozenset({"cancel_upload"}) in commands
    assert any(isinstance(handler, MessageHandler) for handler in early_handlers)
    assert any(
        isinstance(handler, CallbackQueryHandler)
        and getattr(handler, "pattern", None) is not None
        and "clinicdoc" in str(handler.pattern.pattern)
        for handler in early_handlers
    )
