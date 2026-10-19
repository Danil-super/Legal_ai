"""Versioned initial confirmation and report-free presentation, without model I/O."""

import re
from typing import Any
from uuid import UUID

from legal_core.contracts import TelegramPreparationReceipt
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_gateway.ui import back_keyboard


def decode_workflow_confirmation(callback: str) -> tuple[UUID, bool]:
    match = re.fullmatch(r"case:confirm(2?):([0-9a-f-]{36})", callback)
    if match is None:
        raise ValueError("invalid workflow confirmation")
    workflow_id = UUID(match[2])
    if str(workflow_id) != match[2]:
        raise ValueError("noncanonical workflow confirmation")
    return workflow_id, match[1] == "2"


def preparation_workflow_message(wire: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    receipt = TelegramPreparationReceipt.model_validate(wire)
    if receipt.case.intake_schema_version != "dental-case-intake.v2":
        raise ValueError("preparation receipt schema mismatch")
    text = (
        f"Сведения по кейсу {receipt.case.public_number} сохранены. "
        "Это карточка исходных сведений, не итоговый ответ."
    )
    if receipt.case.early_escalation_id is not None:
        text += " Кейс уже направлен юристу."
        action = InlineKeyboardButton(
            "Открыть карточку юриста",
            callback_data=f"case:escalation:{receipt.case.early_escalation_id}",
        )
    else:
        text += " Откройте обработку кейса: бот покажет результат или предложит уточнение."
        action = InlineKeyboardButton(
            "Подготовить рекомендации", callback_data=f"case:analyze:{receipt.case.id}",
        )
    return text, InlineKeyboardMarkup([[action], *back_keyboard().inline_keyboard])
