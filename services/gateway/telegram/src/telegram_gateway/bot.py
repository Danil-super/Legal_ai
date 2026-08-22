"""Safety-scoped Telegram polling gateway."""

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeAlias

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_gateway.ui import (
    HELP_MESSAGE,
    MAIN_MENU_CALLBACKS,
    SCREENS,
    START_MESSAGE,
    TEXT_INPUT_DISABLED_MESSAGE,
    WELCOME_IMAGE,
    back_keyboard,
    main_menu_keyboard,
)

logger = logging.getLogger(__name__)

TelegramApplication: TypeAlias = Application[Any, Any, Any, Any, Any, Any]
TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
READY_FILE = Path("/tmp/telegram-gateway-ready")
ALLOWED_UPDATES = ["message", "callback_query"]


def load_token(environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    token = source.get("TELEGRAM_BOT_TOKEN", "").strip()
    if len(token) < 20 or TOKEN_PATTERN.fullmatch(token) is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing or malformed")
    return token


async def _reply(update: Update, text: str) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    del context
    message = update.effective_message
    if message is not None:
        # Path uploads and inline keyboards follow the official PTB 22.8 APIs.
        # Sources: https://docs.python-telegram-bot.org/en/v22.8/telegram.bot.html
        # https://docs.python-telegram-bot.org/en/v22.8/telegram.inlinekeyboardbutton.html
        await message.reply_photo(
            photo=WELCOME_IMAGE,
            caption=START_MESSAGE,
            reply_markup=main_menu_keyboard(),
        )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    await start(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    del context
    await _reply(update, HELP_MESSAGE)


async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    del context
    await _reply(update, TEXT_INPUT_DISABLED_MESSAGE)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    del context
    query = update.callback_query
    if query is None:
        return

    callback_data = query.data
    allowed_callbacks = MAIN_MENU_CALLBACKS | {"menu"}
    if not isinstance(callback_data, str) or callback_data not in allowed_callbacks:
        await query.answer("Меню обновилось. Откройте /menu.", show_alert=True)
        return

    # Callback queries must be answered, even when no toast is needed.
    # Source: https://docs.python-telegram-bot.org/en/v22.7/examples.inlinekeyboard.html
    await query.answer()
    keyboard = main_menu_keyboard() if callback_data == "menu" else back_keyboard()
    await query.edit_message_caption(caption=SCREENS[callback_data], reply_markup=keyboard)


async def on_startup(application: TelegramApplication) -> None:
    # PTB 22.8 runs post_init after Bot.initialize/getMe and before polling. Cosmetic,
    # rate-limited profile mutations live in telegram_gateway.profile and are never
    # allowed to make polling unavailable.
    # Source: https://docs.python-telegram-bot.org/en/v22.8/telegram.ext.application.html
    del application
    READY_FILE.touch(mode=0o600)
    logger.info("telegram gateway initialized")


async def on_shutdown(application: TelegramApplication) -> None:
    del application
    READY_FILE.unlink(missing_ok=True)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    del update
    logger.error("telegram update failed: %s", type(context.error).__name__)


def build_application(token: str) -> TelegramApplication:
    # Application.builder + async handlers follows the official v22.8 pattern.
    # Source: https://docs.python-telegram-bot.org/en/v22.8/examples.echobot.html
    application = (
        Application.builder()
        .token(token)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(menu_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    application.add_error_handler(on_error)
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    application = build_application(load_token())
    # Limit the Bot API subscription to messages; unused update types are not requested.
    # Source: https://docs.python-telegram-bot.org/en/v22.8/telegram.ext.application.html
    application.run_polling(
        allowed_updates=ALLOWED_UPDATES,
        bootstrap_retries=3,
        drop_pending_updates=False,
    )
