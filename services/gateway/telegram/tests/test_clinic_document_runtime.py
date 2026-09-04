import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
)

import telegram_gateway.clinic_document_runtime as clinic_document_runtime
from telegram_gateway.clinic_document_runtime import (
    build_application_with_clinic_documents,
    parse_upload_command,
    retire_confirmation_keyboard,
    retire_keyboard,
    review_clinic_document_callback,
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


def test_retirement_keyboards_are_explicit_and_bounded() -> None:
    start = retire_keyboard(VERSION_ID)
    confirm = retire_confirmation_keyboard(VERSION_ID)

    assert start.inline_keyboard[0][0].callback_data == f"clinicdoc:retire:{VERSION_ID}"
    assert confirm.inline_keyboard[0][0].callback_data == f"clinicdoc:retire-confirm:{VERSION_ID}"
    assert confirm.inline_keyboard[1][0].callback_data == f"clinicdoc:retire-cancel:{VERSION_ID}"
    assert all(
        len(button.callback_data.encode()) <= 64
        for row in confirm.inline_keyboard
        for button in row
        if button.callback_data is not None
    )


class _FakeCallbackQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answers = 0
        self.edits: list[tuple[str, object | None]] = []

    async def answer(self) -> None:
        self.answers += 1

    async def edit_message_text(self, text: str, reply_markup: object | None = None) -> None:
        self.edits.append((text, reply_markup))


def test_retirement_requires_confirmation_before_legal_core_write() -> None:
    query = _FakeCallbackQuery(f"clinicdoc:retire:{VERSION_ID}")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
    )

    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(review_clinic_document_callback(update, None))

    assert query.answers == 1
    assert "СНЯТЬ ВЕРСИЮ" in query.edits[0][0]
    assert query.edits[0][1] is not None


def test_confirmed_retirement_appends_retired_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCore:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.closed = False

        async def review_version(self, **kwargs: object) -> dict[str, str]:
            self.calls.append(kwargs)
            return {"decision": "RETIRED"}

        async def aclose(self) -> None:
            self.closed = True

    core = FakeCore()
    monkeypatch.setattr(clinic_document_runtime, "ClinicDocumentCoreClient", lambda: core)
    query = _FakeCallbackQuery(f"clinicdoc:retire-confirm:{VERSION_ID}")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
    )

    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(review_clinic_document_callback(update, None))

    assert core.calls == [
        {
            "telegram_user_id": 7_000_000_001,
            "version_id": VERSION_ID,
            "decision": "RETIRED",
            "reason_code": "CLINIC_DOCUMENT_RETIRED",
        }
    ]
    assert core.closed is True
    assert "снята с использования" in query.edits[0][0]


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
        and "retire-confirm" in str(handler.pattern.pattern)
        for handler in early_handlers
    )
