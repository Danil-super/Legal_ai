# ruff: noqa: RUF001
"""Production composition: quick confirmed intake and optional case work notes."""

import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, CommandHandler, ContextTypes

from telegram_gateway import bot as gateway_bot
from telegram_gateway.analysis_diagnostics_runtime import build_application_with_diagnostics
from telegram_gateway.case_work_notes import install_work_notes
from telegram_gateway.guided_intake_runtime import install_guided_intake
from telegram_gateway.ui import HELP_MESSAGE


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    gateway_bot._clear_pending_inputs(context)
    await gateway_bot._reply(
        update, HELP_MESSAGE + "\n\n/analysis_status — диагностика (владелец клиники).\n"
        "Быстрое описание: проверьте карточку, исправьте нужное поле и подтвердите. "
        "Вопросы задаются только о недостающих сведениях.\n"
        "В кейсе, переданном специалисту: «Следующий шаг», «Комментарий специалиста» "
        "и подтверждение итога перед завершением. Эти записи необязательны.",
    )
    raise ApplicationHandlerStop


def build_application_with_case_experience(token: str) -> gateway_bot.TelegramApplication:
    application = build_application_with_diagnostics(token)
    install_guided_intake(application)
    install_work_notes(application)
    application.add_handler(CommandHandler("help", help_command), group=-8)
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    application = build_application_with_case_experience(gateway_bot.load_token())
    application.run_polling(
        allowed_updates=gateway_bot.ALLOWED_UPDATES,
        bootstrap_retries=3, drop_pending_updates=False,
    )
