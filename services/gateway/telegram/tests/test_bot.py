import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telegram import InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler
from telegram_gateway.bot import (
    ADMIN_GRANT_ACCESS_KEY,
    ALLOWED_UPDATES,
    admin_panel,
    build_application,
    help_command,
    load_token,
    menu_callback,
    on_startup,
    start,
    text_input,
)
from telegram_gateway.ui import (
    AVATAR_IMAGE,
    BOT_DESCRIPTION,
    BOT_NAME,
    BOT_SHORT_DESCRIPTION,
    HELP_MESSAGE,
    MAIN_MENU_CALLBACKS,
    SCREENS,
    START_MESSAGE,
    TEXT_INPUT_DISABLED_MESSAGE,
    WELCOME_IMAGE,
    admin_panel_keyboard,
    main_menu_keyboard,
)


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.text_replies: list[tuple[str, InlineKeyboardMarkup | None]] = []
        self.photo_replies: list[tuple[Path, str, InlineKeyboardMarkup]] = []

    async def reply_text(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        self.text_replies.append((text, reply_markup))

    async def reply_photo(
        self,
        photo: Path,
        caption: str,
        reply_markup: InlineKeyboardMarkup,
    ) -> None:
        self.photo_replies.append((photo, caption, reply_markup))


class FakeCallbackQuery:
    def __init__(self, data: object) -> None:
        self.data = data
        self.answers: list[tuple[str | None, bool]] = []
        self.edits: list[tuple[str, InlineKeyboardMarkup]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))

    async def edit_message_caption(
        self,
        caption: str,
        reply_markup: InlineKeyboardMarkup,
    ) -> None:
        self.edits.append((caption, reply_markup))


class FakeUpdate:
    def __init__(
        self,
        message: FakeMessage | None = None,
        callback_query: FakeCallbackQuery | None = None,
    ) -> None:
        self.effective_message = message
        self.callback_query = callback_query


def test_load_token_returns_stripped_configured_value() -> None:
    token = load_token({"TELEGRAM_BOT_TOKEN": "  123456:unit_test_token_value_1234567890  "})

    assert token == "123456:unit_test_token_value_1234567890"


@pytest.mark.parametrize(
    "environment",
    [{}, {"TELEGRAM_BOT_TOKEN": ""}, {"TELEGRAM_BOT_TOKEN": "not-a-token"}],
)
def test_load_token_rejects_missing_or_malformed_values(environment: dict[str, str]) -> None:
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        load_token(environment)


def test_start_sends_branded_image_caption_and_main_menu() -> None:
    start_message = FakeMessage()

    asyncio.run(start(FakeUpdate(start_message), None))

    assert start_message.photo_replies == [
        (WELCOME_IMAGE, START_MESSAGE, main_menu_keyboard()),
    ]
    assert WELCOME_IMAGE.is_file()
    assert "персональ" in START_MESSAGE.lower()


def test_help_returns_concise_safety_scoped_instructions() -> None:
    help_message = FakeMessage()

    asyncio.run(help_command(FakeUpdate(help_message), None))

    assert help_message.text_replies == [(HELP_MESSAGE, None)]
    assert "чернов" in HELP_MESSAGE.lower()
    assert "/describe_case" in HELP_MESSAGE


def test_free_text_is_not_echoed_or_processed_before_case_core() -> None:
    sensitive_input = "Пациент Иван Иванов сообщил медицинские сведения"
    message = FakeMessage(sensitive_input)

    asyncio.run(text_input(FakeUpdate(message), None))

    assert message.text_replies == [(TEXT_INPUT_DISABLED_MESSAGE, None)]
    assert sensitive_input not in message.text_replies[0][0]


def test_main_menu_exposes_frequent_actions_as_clear_allowlisted_buttons() -> None:
    keyboard = main_menu_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert len(buttons) == 9
    assert {button.callback_data for button in buttons} <= MAIN_MENU_CALLBACKS
    assert {"case:start", "quick:start", "case:drafts", "account:id", "help"} <= {
        button.callback_data for button in buttons
    }
    assert all(button.text.strip() for button in buttons)
    assert all(
        isinstance(button.callback_data, str) and len(button.callback_data.encode()) <= 64
        for button in buttons
    )


def test_lawyer_menu_exposes_only_review_workspace_actions() -> None:
    keyboard = main_menu_keyboard("CLINIC_LAWYER")
    callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row}

    assert "case:escalations" in callbacks
    assert "case:start" not in callbacks
    assert "quick:start" not in callbacks
    assert "case:drafts" not in callbacks


def test_known_callback_answers_and_edits_the_welcome_caption() -> None:
    query = FakeCallbackQuery("privacy")

    asyncio.run(menu_callback(FakeUpdate(callback_query=query), None))

    assert query.answers == [(None, False)]
    assert query.edits[0][0] == SCREENS["privacy"]
    assert query.edits[0][1].inline_keyboard[0][0].callback_data == "menu"


def test_identity_button_displays_the_current_users_telegram_id() -> None:
    query = FakeCallbackQuery("account:id")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
    )

    asyncio.run(menu_callback(update, None))

    assert query.answers == [(None, False)]
    assert "7000000001" in query.edits[0][0]
    assert query.edits[0][1].inline_keyboard[0][0].callback_data == "menu"


def test_returning_to_menu_clears_pending_owner_access_input() -> None:
    query = FakeCallbackQuery("menu")
    context = SimpleNamespace(user_data={ADMIN_GRANT_ACCESS_KEY: True})

    asyncio.run(menu_callback(FakeUpdate(callback_query=query), context))

    assert context.user_data == {}


def test_admin_command_opens_a_separate_owner_workspace() -> None:
    message = FakeMessage()

    asyncio.run(admin_panel(FakeUpdate(message), None))

    assert "панель владельца" in message.text_replies[0][0].lower()
    assert message.text_replies[0][1] == admin_panel_keyboard()


@pytest.mark.parametrize("data", ["unknown", 42, None])
def test_untrusted_callback_data_is_rejected_without_editing(data: object) -> None:
    query = FakeCallbackQuery(data)

    asyncio.run(menu_callback(FakeUpdate(callback_query=query), None))

    assert query.answers == [("Меню обновилось. Откройте /menu.", True)]
    assert query.edits == []


def test_bot_profile_copy_and_captions_fit_telegram_limits() -> None:
    assert BOT_NAME == "Dental Legal AI"
    assert 1 <= len(BOT_SHORT_DESCRIPTION) <= 120
    assert 1 <= len(BOT_DESCRIPTION) <= 512
    assert all(len(caption) <= 1024 for caption in SCREENS.values())
    assert AVATAR_IMAGE.is_file()
    assert AVATAR_IMAGE.suffix == ".jpg"


def test_application_registers_callback_handler_and_required_update_types() -> None:
    application = build_application("123456:unit_test_token_value_1234567890")
    handlers: list[Any] = [handler for group in application.handlers.values() for handler in group]

    assert any(isinstance(handler, CallbackQueryHandler) for handler in handlers)
    assert any(
        isinstance(handler, CommandHandler) and "grant_access" in handler.commands
        for handler in handlers
    )
    assert any(
        isinstance(handler, CommandHandler) and "admin" in handler.commands
        for handler in handlers
    )
    assert ALLOWED_UPDATES == ["message", "callback_query"]


def test_polling_startup_does_not_repeat_rate_limited_profile_mutations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ready_file = tmp_path / "ready"
    monkeypatch.setattr("telegram_gateway.bot.READY_FILE", ready_file)

    class StartupApplication:
        """No Bot API mutation methods are intentionally available here."""

    asyncio.run(on_startup(StartupApplication()))  # type: ignore[arg-type]

    assert ready_file.is_file()
