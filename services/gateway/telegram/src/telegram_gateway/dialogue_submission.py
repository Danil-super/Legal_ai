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
        await bot._reply(update, "Кейс передан юристу. Он проверит ситуацию и подготовит ответ.",
                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                             "Открыть кейс", callback_data=f"case:escalation:{escalation}",
                         )], *back_keyboard().inline_keyboard]))
        return
    await enqueue_analysis(update, context, receipt.case.id)
