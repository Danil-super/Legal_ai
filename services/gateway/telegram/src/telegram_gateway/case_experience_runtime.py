# ruff: noqa: RUF001
"""Production composition: quick confirmed intake and optional case work notes."""

import logging
import os

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


def build_application_with_case_experience(
    token: str, *, enable_understanding: bool = False, enable_answer_interpretation: bool = False,
    enable_simplified_dialogue: bool = False,
    enable_lawyer_work_products: bool = False,
) -> gateway_bot.TelegramApplication:
    if enable_answer_interpretation and not enable_understanding:
        raise ValueError("Answer interpretation requires shared preparation")
    if enable_simplified_dialogue and not enable_answer_interpretation:
        raise ValueError("Simplified dialogue requires complete interpretation")
    application = build_application_with_diagnostics(token)
    application.bot_data["intake_simplified_dialogue"] = enable_simplified_dialogue
    install_guided_intake(application)
    if enable_understanding:
        from telegram_gateway.preparation_form_runtime import install_preparation_form
        from telegram_gateway.understanding_runtime import install_understanding

        install_preparation_form(application)
        install_understanding(application)
        if enable_answer_interpretation:
            from telegram_gateway.answer_runtime import install_answer_interpretation

            install_answer_interpretation(application)
    if enable_simplified_dialogue:
        from telegram_gateway.clarification_runtime import install_inline_clarification

        install_inline_clarification(application)
    install_work_notes(application)
    if enable_lawyer_work_products:
        from telegram_gateway.lawyer_work_product_runtime import install

        install(application)
    application.add_handler(CommandHandler("help", help_command), group=-8)
    return application


def main() -> None:
    value = os.getenv("PRE_REPORT_INTAKE_ENABLED", "0")
    if value not in {"0", "1"}:
        raise RuntimeError("PRE_REPORT_INTAKE_ENABLED must be exactly 0 or 1")
    enabled = value == "1"
    draft_flag = os.getenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "0")
    if draft_flag not in {"0", "1"}:
        raise RuntimeError("AI_FIRST_LAWYER_DRAFT_ENABLED must be exactly 0 or 1")
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    application = build_application_with_case_experience(
        gateway_bot.load_token(), enable_understanding=enabled,
        enable_answer_interpretation=enabled,
        enable_simplified_dialogue=enabled,
        **({"enable_lawyer_work_products": True} if draft_flag == "1" else {}),
    )
    application.run_polling(
        allowed_updates=gateway_bot.ALLOWED_UPDATES,
        bootstrap_retries=3, drop_pending_updates=False,
    )
