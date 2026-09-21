# ruff: noqa: RUF001
"""Optional short work notes and confirmed closure in the existing lawyer workspace.

Notes use the existing authorized discussion/closure APIs and retention policy.
They are human statements, not evidence, automatic risk overrides or scheduled jobs.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from legal_core.pseudonymization import pseudonymize_text
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_gateway import bot as gateway_bot
from telegram_gateway import escalation_workspace as workspace
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.quick_intake import contains_probable_person_name
from telegram_gateway.ui import back_keyboard

logger = logging.getLogger(__name__)
KINDS = {
    "plan": (
        "Следующий шаг",
        "Что сделать и к какому внутреннему сроку? Напишите одним сообщением. "
        "Ответственный — вы, назначенный по кейсу. Срок здесь — ваша рабочая заметка, "
        "не автоматический расчёт правового срока. Напоминание не создаётся.",
        "РАБОЧИЙ ПЛАН. Ответственный: автор записи, назначенный по кейсу. ",
    ),
    "review": (
        "Комментарий специалиста",
        "Кратко: что проверено, что установлено, что пока неизвестно и почему вы так считаете. "
        "Отделяйте сообщение пациента от результата проверки. Проблемы документации "
        "не записывайте автоматически как установленный дефект лечения.",
        "МНЕНИЕ СПЕЦИАЛИСТА (не меняет автоматическую оценку риска). ",
    ),
    "close": (
        "Итог обращения",
        "Напишите итог одним сообщением. При необходимости добавьте: фактически выплачено; "
        "дополнительные расходы; причина обращения; что изменить в работе клиники. "
        "Неизвестные суммы не считайте нулевыми, требования не смешивайте с выплатами. "
        "Необязательные пункты можно пропустить. Сейчас кейс ещё НЕ будет закрыт — "
        "сначала покажу текст для подтверждения.",
        "ИТОГ ПО ПРОВЕРКЕ ОТВЕТСТВЕННОГО СОТРУДНИКА. ",
    ),
}


def note_body(kind: str, raw: str) -> str:
    if kind not in KINDS:
        raise ValueError("Неизвестный вид записи.")
    if contains_probable_person_name(raw):
        raise ValueError("Уберите ФИО и персональные сведения.")
    text = pseudonymize_text(raw).text.strip()
    if not 1 <= len(text) <= 1200:
        raise ValueError("Введите от 1 до 1200 символов без персональных данных.")
    return KINDS[kind][2] + text


def _pending(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    value = gateway_bot._user_data(context).get(workspace.RESOLUTION_KEY)
    return value if isinstance(value, dict) and value.get("workNoteVersion") == 1 else None


def _return_keyboard(escalation_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "← Отмена, к кейсу", callback_data=f"case:escalation:{escalation_id}",
        )],
        *back_keyboard().inline_keyboard,
    ])


async def _show_workspace(
    update: Update, context: ContextTypes.DEFAULT_TYPE, escalation_id: UUID,
) -> None:
    await workspace.show_workspace(update, context, escalation_id)
    if gateway_bot._user_data(context).get(workspace.DISCUSSION_KEY) == str(escalation_id):
        await gateway_bot._reply(
            update, "Дополнительно для ответственного специалиста — необязательно:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "📌 Следующий шаг", callback_data=f"esc:plan:{escalation_id}",
                )],
                [InlineKeyboardButton(
                    "📝 Комментарий специалиста", callback_data=f"esc:review:{escalation_id}",
                )],
            ]),
        )


async def workspace_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback = await gateway_bot._answer_callback(update)
    actor = gateway_bot._actor_id(update)
    if callback is None or actor is None:
        raise ApplicationHandlerStop
    if gateway_bot.WIZARD_DATA_KEY in gateway_bot._user_data(context):
        await gateway_bot._reply(update, "Сначала завершите черновик или откройте /menu.")
        raise ApplicationHandlerStop
    try:
        escalation_id = UUID(callback.rsplit(":", 1)[1])
        if callback.startswith("esc:claim:"):
            await gateway_bot._legal_core(context).claim_escalation(escalation_id, actor)
        await _show_workspace(update, context, escalation_id)
    except (ValueError, LegalCoreApiError):
        logger.warning("case workspace unavailable")
        await gateway_bot._reply(
            update, "Не удалось открыть кейс. Обновите список обращений.",
            reply_markup=back_keyboard(),
        )
    raise ApplicationHandlerStop


async def begin_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback = await gateway_bot._answer_callback(update)
    actor = gateway_bot._actor_id(update)
    if callback is None or actor is None:
        raise ApplicationHandlerStop
    if gateway_bot.WIZARD_DATA_KEY in gateway_bot._user_data(context):
        await gateway_bot._reply(update, "Сначала завершите черновик или откройте /menu.")
        raise ApplicationHandlerStop
    try:
        _, kind, raw_id = callback.split(":")
        kind = "close" if kind == "resolve" else kind
        if kind not in KINDS:
            raise ValueError("invalid kind")
        escalation_id = UUID(raw_id)
        client = gateway_bot._legal_core(context)
        detail = await client.get_escalation(escalation_id, actor)
        membership = await client.get_actor(actor)
        if (detail.get("status") != "IN_PROGRESS" or detail.get("assignedToMe") is not True
                or membership.get("role") not in {"CLINIC_OWNER", "CLINIC_LAWYER"}):
            await gateway_bot._reply(
                update, "Запись доступна текущему ответственному специалисту "
                "по незавершённому кейсу. Сначала возьмите его в работу.",
            )
            raise ApplicationHandlerStop
        gateway_bot._clear_pending_inputs(context)
        data = gateway_bot._user_data(context)
        data[workspace.DISCUSSION_KEY] = str(escalation_id)
        data[workspace.RESOLUTION_KEY] = {
            "workNoteVersion": 1, "escalationId": str(escalation_id),
            "kind": kind, "nonce": uuid4().hex[:12], "body": None,
        }
        await gateway_bot._reply(
            update, f"{KINDS[kind][0]}\n\n{KINDS[kind][1]}\n\nБез ФИО и файлов пациента.",
            reply_markup=_return_keyboard(escalation_id),
        )
    except (ValueError, LegalCoreApiError):
        logger.warning("work note unavailable")
        await gateway_bot._reply(update, "Не удалось открыть действие. Обновите карточку кейса.")
    raise ApplicationHandlerStop


async def receive_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = _pending(context)
    if pending is None:
        return
    if gateway_bot.WIZARD_DATA_KEY in gateway_bot._user_data(context):
        gateway_bot._user_data(context).pop(workspace.RESOLUTION_KEY, None)
        return
    if gateway_bot._user_data(context).get(workspace.DISCUSSION_KEY) != pending["escalationId"]:
        gateway_bot._user_data(context).pop(workspace.RESOLUTION_KEY, None)
        raise ApplicationHandlerStop
    try:
        body = note_body(pending["kind"], gateway_bot._message_text(update))
    except ValueError as exc:
        await gateway_bot._reply(update, str(exc))
        raise ApplicationHandlerStop from None
    pending["body"] = body
    pending["nonce"] = uuid4().hex[:12]
    closing = pending["kind"] == "close"
    await gateway_bot._reply(
        update, "Проверьте запись:\n\n" + body + (
            "\n\nТолько после подтверждения обращение будет завершено. "
            "Пациенту ничего не отправляется."
            if closing else "\n\nЗапись останется в истории с автором и временем. "
            "Она не меняет факты и риск автоматического анализа."
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Подтвердить и завершить" if closing else "✅ Сохранить в истории",
                callback_data=f"esc:note-save:{pending['nonce']}",
            )],
            *_return_keyboard(UUID(pending["escalationId"])).inline_keyboard,
        ]),
    )
    raise ApplicationHandlerStop


async def save_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback = await gateway_bot._answer_callback(update)
    actor = gateway_bot._actor_id(update)
    pending = _pending(context)
    if (
        callback is None or actor is None or pending is None or not pending.get("body")
        or callback.rsplit(":", 1)[1] != pending.get("nonce")
        or gateway_bot._user_data(context).get(workspace.DISCUSSION_KEY) != pending["escalationId"]
    ):
        await gateway_bot._reply(update, "Эта запись уже изменена, отменена или сохранена.")
        raise ApplicationHandlerStop
    escalation_id = UUID(pending["escalationId"])
    client = gateway_bot._legal_core(context)
    try:
        detail = await client.get_escalation(escalation_id, actor)
        membership = await client.get_actor(actor)
        if (detail.get("status") != "IN_PROGRESS" or detail.get("assignedToMe") is not True
                or membership.get("role") not in {"CLINIC_OWNER", "CLINIC_LAWYER"}):
            raise ValueError("assignment changed")
        # Existing discussion writes are not idempotent. Consume the preview before POST:
        # never replay an ambiguous timed-out write or double-click automatically.
        gateway_bot._user_data(context).pop(workspace.RESOLUTION_KEY, None)
        if pending["kind"] == "close":
            await client.resolve_escalation(escalation_id, actor, body=pending["body"])
            gateway_bot._user_data(context).pop(workspace.DISCUSSION_KEY, None)
        else:
            await client.post_escalation_discussion_message(
                escalation_id, actor, body=pending["body"],
            )
        await gateway_bot._reply(update, "✅ Итог сохранён, обращение завершено." if (
            pending["kind"] == "close"
        ) else "✅ Запись сохранена в истории кейса.")
    except (ValueError, LegalCoreApiError):
        gateway_bot._user_data(context).pop(workspace.RESOLUTION_KEY, None)
        logger.warning("work note write not confirmed")
        await gateway_bot._reply(
            update, "Не удалось подтвердить запись. Откройте историю перед повтором: "
            "при сетевой ошибке она могла сохраниться. Автоматического повтора не будет.",
            reply_markup=_return_keyboard(escalation_id),
        )
        raise ApplicationHandlerStop from None
    await _show_workspace(update, context, escalation_id)
    raise ApplicationHandlerStop


def install_work_notes(application: gateway_bot.TelegramApplication) -> None:
    application.add_handler(CallbackQueryHandler(
        workspace_entry, pattern=r"^(?:case:escalation|esc:claim):[0-9a-f-]{36}$",
    ), group=-6)
    application.add_handler(CallbackQueryHandler(
        begin_note, pattern=r"^esc:(?:plan|review|resolve):[0-9a-f-]{36}$",
    ), group=-6)
    application.add_handler(CallbackQueryHandler(
        save_note, pattern=r"^esc:note-save:[0-9a-f]{12}$",
    ), group=-6)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, receive_note,
    ), group=-6)
