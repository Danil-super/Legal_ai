# ruff: noqa: RUF001
"""Second bot entrypoint: OFF by default; allowlisted synthetic navigation only.

Not imported into telegram_gateway.__main__ or any clinic handler composition.
It neither reads message text/files nor persists case data. Fixed examples only.
"""

import logging
import os
from collections.abc import Mapping
from typing import Any

from legal_core.personal.catalog import CATALOG, PREVIEW_NOTICE, get_topic, render_demo
from legal_core.personal.contracts import Audience
from legal_core.personal.settings import PreviewSettings, personal_bot_token
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

PreviewApplication = Application[Any, Any, Any, Any, Any, Any]
logger = logging.getLogger(__name__)


def _home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Я пациент", callback_data="pp:audience:PATIENT")],
        [InlineKeyboardButton("Я сотрудник", callback_data="pp:audience:EMPLOYEE")],
    ])


def build_application(env: Mapping[str, str]) -> PreviewApplication | None:
    config = PreviewSettings.from_mapping(env)
    token = personal_bot_token(env, config)
    if token is None:
        return None
    application: PreviewApplication = Application.builder().token(token).concurrent_updates(
        False,
    ).build()

    def require_tester(update: Update) -> None:
        actor = update.effective_user
        chat = update.effective_chat
        if (
            actor is None or chat is None or chat.type != "private" or chat.id != actor.id
            or not config.allows_tester(actor.id)
        ):
            raise ApplicationHandlerStop

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        require_tester(update)
        message = update.effective_message
        if message is not None:
            await message.reply_text(PREVIEW_NOTICE, reply_markup=_home())

    async def navigate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        require_tester(update)
        query = update.callback_query
        if query is None or not isinstance(query.data, str):
            return
        await query.answer()
        if query.data == "pp:home":
            await query.edit_message_text(PREVIEW_NOTICE, reply_markup=_home())
            return
        if query.data.startswith("pp:audience:"):
            try:
                audience = Audience(query.data.removeprefix("pp:audience:"))
            except ValueError:
                return
            rows = [[InlineKeyboardButton(card.title, callback_data=f"pp:demo:{card.topic.value}")]
                    for card in CATALOG if card.audience is audience]
            rows.append([InlineKeyboardButton("Назад", callback_data="pp:home")])
            await query.edit_message_text(PREVIEW_NOTICE, reply_markup=InlineKeyboardMarkup(rows))
            return
        card = get_topic(query.data.removeprefix("pp:demo:"))
        if card is not None:
            await query.edit_message_text(render_demo(card), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Назад", callback_data=f"pp:audience:{card.audience.value}")],
            ]))

    async def reject_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        require_tester(update)
        # Do not access text, caption, document, contact, file_id or user profile fields.
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                "Личные обращения и загрузка файлов ещё закрыты. "
                "Используйте только кнопки вымышленных примеров; "
                "не отправляйте сведения о себе или других людях.",
                reply_markup=_home(),
            )

    async def failed(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        # Avoid exception text, Update repr, response bodies and Telegram token URLs.
        logger.warning("personal preview operation failed")
        if not isinstance(update, Update):
            return
        try:
            require_tester(update)
            if update.effective_message is not None:
                await update.effective_message.reply_text("Пример недоступен. Откройте /start.")
        except (ApplicationHandlerStop, TelegramError):
            return

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(navigate, pattern=r"^pp:"))
    application.add_handler(MessageHandler(filters.ALL, reject_input))
    application.add_error_handler(failed)
    return application


def main() -> None:
    application = build_application(os.environ)
    if application is None:
        # No token/network/DB/provisioning in default mode.
        return
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    application.run_polling(allowed_updates=["message", "callback_query"],
                            drop_pending_updates=True, bootstrap_retries=0)


if __name__ == "__main__":
    main()
