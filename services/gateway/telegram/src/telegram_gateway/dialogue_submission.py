"""Start the next step after explicit final-summary consent, not after each answer."""

from typing import Any

from legal_core.contracts import TelegramPreparationReceipt
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from telegram_gateway import bot
from telegram_gateway.analysis_jobs_runtime import enqueue_analysis
from telegram_gateway.ui import back_keyboard


async def present_submission(
    update: Update, context: ContextTypes.DEFAULT_TYPE, workflow: dict[str, Any],
) -> None:
    receipt = TelegramPreparationReceipt.model_validate(workflow)
    if receipt.case.intake_schema_version != "dental-case-intake.v2":
        raise ValueError("simplified dialogue requires a V2 preparation receipt")
    escalation = receipt.case.early_escalation_id
    if escalation is not None:
        prepare_plan = context.bot_data.get("ai_first_lawyer_draft_enabled") is True
        text = ("Кейс передан юристу. Готовлю предварительный разбор ИИ: "
                "действия и нормативные основания для проверки юристом." if prepare_plan else
                "Кейс передан юристу. Он проверит ситуацию и подготовит ответ.")
        await bot._reply(update, text,
                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                             "Открыть кейс", callback_data=f"case:escalation:{escalation}",
                         )], *back_keyboard().inline_keyboard]))
        if not prepare_plan:
            return
    await enqueue_analysis(update, context, receipt.case.id)
