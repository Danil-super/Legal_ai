"""Checklist selections update the same message after persistence; Continue advances it."""

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError

_FIELDS = {"SITUATION": "situationAreas", "CLINIC_ACTIONS": "clinicActions",
           "HEALTH": "healthSignals"}


def selection_field(update: Update, state: object) -> str | None:
    query = update.callback_query
    if query is None or not isinstance(query.data, str):
        return None
    parts = query.data.split(":", 4)
    if (len(parts) == 5 and parts[:3] == ["case", "v2", "multi"]
            and parts[4] != "DONE" and _FIELDS.get(str(state)) == parts[3]):
        return parts[3]
    return None


async def render_selection(update: Update, data: dict[str, Any],
                           keyboard: InlineKeyboardMarkup) -> None:
    query = update.callback_query
    if query is None:
        return
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except BadRequest as error:
        if "message is not modified" in str(error).lower():
            return
        await _reopen(update, data)
    except TelegramError:
        await _reopen(update, data)


async def _reopen(update: Update, data: dict[str, Any]) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Выбор сохранён, но кнопки не обновились. Откройте карточку снова.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Открыть текущий черновик",
                                      callback_data=f"case:draft:{data['draft_id']}")],
                [InlineKeyboardButton("← Главное меню", callback_data="menu")],
            ]))
