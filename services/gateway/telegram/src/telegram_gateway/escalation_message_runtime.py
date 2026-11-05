"""Stable private-message intents and explicit recovery after an uncertain response."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop, ContextTypes

from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.ui import back_keyboard

PENDING_KEY = "escalation_message_operation"
RETRY_PATTERN = r"^esc:retrymsg:[0-9a-f]{32}:[mc]:[0-9]{1,10}$"


def message_operation(actor: int, escalation: UUID, resolving: bool, source: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"dental-legal:escalation:{escalation}:actor:{actor}:"
        f"action:{'resolve' if resolving else 'discussion'}:message:{source}",
    )


def pending_operation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, escalation: UUID, body: str, resolving: bool
) -> tuple[UUID, int | None]:
    data = gateway_bot._user_data(context)
    binding = {
        "escalation": str(escalation),
        "resolving": resolving,
        "bodySha256": hashlib.sha256(" ".join(body.split()).encode()).hexdigest(),
    }
    pending = data.get(PENDING_KEY)
    if isinstance(pending, dict) and all(pending.get(k) == v for k, v in binding.items()):
        return UUID(pending["operationId"]), pending["sourceMessageId"]
    source = getattr(update.effective_message, "message_id", None)
    source = source if type(source) is int and 0 < source < 10_000_000_000 else None
    actor = gateway_bot._actor_id(update)
    assert actor is not None
    operation = message_operation(actor, escalation, resolving, source) if source else uuid4()
    data[PENDING_KEY] = {**binding, "operationId": str(operation), "sourceMessageId": source}
    return operation, source


async def submit_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    escalation: UUID,
    body: str,
    resolving: bool,
    operation: UUID,
    source: int | None,
) -> None:
    from telegram_gateway.analysis_runtime import _active_discussion_keyboard

    actor = gateway_bot._actor_id(update)
    assert actor is not None
    data = gateway_bot._user_data(context)
    try:
        client = gateway_bot._legal_core(context)
        method = (
            client.resolve_escalation if resolving else client.post_escalation_discussion_message
        )
        await method(escalation, actor, body=body, operation_id=operation)
    except LegalCoreApiError as exc:
        if exc.code == "DIRECT_IDENTIFIER_NOT_ALLOWED":
            detail = ("\u041d\u0435 сохраняю сообщение: удалите ФИО, контакты "
                      "и номера документов пациента.")
        elif exc.code in {"ESCALATION_NOT_FOUND", "ESCALATION_RESOLVED"}:
            data.pop("escalation_discussion_id", None)
            data.pop("escalation_resolution_pending", None)
            detail = "Этот кейс больше недоступен для обсуждения."
        elif exc.status_code >= 500:
            kwargs: dict[str, Any] = {}
            if source is not None:
                callback = f"esc:retrymsg:{escalation.hex}:{'c' if resolving else 'm'}:{source}"
                kwargs = {
                    "reply_to_message_id": source,
                    "reply_markup": InlineKeyboardMarkup(
                        [
                            [InlineKeyboardButton("🔄 Проверить отправку", callback_data=callback)],
                            [
                                InlineKeyboardButton(
                                "← \u041a кейсу", callback_data=f"case:escalation:{escalation}"
                                )
                            ],
                        ]
                    ),
                }
            message = update.effective_message
            if message is not None:
                try:
                    await message.reply_text(
                        "\u041d\u0435 удалось проверить отправку. Нажмите «Проверить "
                        "отправку» или повторите тот же текст — дубля не будет.", **kwargs,
                    )
                except BadRequest:
                    await gateway_bot._reply(update, "Откройте историю кейса и проверьте "
                                             "результат отправки.", reply_markup=back_keyboard())
            raise ApplicationHandlerStop from exc
        else:
            detail = (
                "\u041d\u0435 удалось сохранить сообщение. Откройте карточку и проверьте результат."
            )
        await gateway_bot._reply(update, f"⚠️ {detail}")
        raise ApplicationHandlerStop from exc
    pending = data.get(PENDING_KEY)
    if isinstance(pending, dict) and pending.get("operationId") == str(operation):
        data.pop(PENDING_KEY, None)
    if resolving:
        for key in ("escalation_discussion_id", "escalation_resolution_pending"):
            if data.get(key) == str(escalation):
                data.pop(key, None)
    await gateway_bot._reply(
        update,
        "✅ Обращение завершено. Итог сохранён в истории."
        if resolving
        else "✅ Сообщение добавлено. Можно написать следующий обезличенный вопрос или ответ.",
        reply_markup=back_keyboard() if resolving else _active_discussion_keyboard(escalation),
    )
    raise ApplicationHandlerStop


async def retry_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        raise ApplicationHandlerStop
    await query.answer()
    original = getattr(query.message, "reply_to_message", None)
    actor = gateway_bot._actor_id(update)
    try:
        _, _, raw_escalation, mode, raw_source = str(query.data).split(":")
        source, escalation = int(raw_source), UUID(raw_escalation)
        if (
            mode not in {"m", "c"}
            or actor is None
            or original is None
            or original.message_id != source
            or original.from_user.id != actor
            or original.chat.type != "private"
            or not isinstance(original.text, str)
        ):
            raise ValueError("unavailable original message")
    except (ValueError, AttributeError, TypeError):
        await gateway_bot._reply(
            update,
            "Исходное сообщение недоступно. Откройте историю кейса и проверьте результат отправки.",
            reply_markup=back_keyboard(),
        )
        raise ApplicationHandlerStop from None
    await submit_message(
        update,
        context,
        escalation=escalation,
        body=original.text,
        resolving=mode == "c",
        source=source,
        operation=message_operation(actor, escalation, mode == "c", source),
    )
