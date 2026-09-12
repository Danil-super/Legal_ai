# ruff: noqa: RUF001
"""Tenant-authorized lawyer workspace. Legal Core owns every case decision."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Any
from uuid import UUID

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.ui import back_keyboard

DISCUSSION_KEY = "escalation_discussion_id"
RESOLUTION_KEY = "escalation_resolution_pending"
REASONS = {
    "HOSPITALIZATION_REPORTED": "Госпитализация",
    "FORMAL_CLAIM_RECEIVED": "Получена письменная претензия",
    "HARM_CLAIMED": "Заявлен вред здоровью",
    "REGULATOR_OR_COURT_INVOLVED": "Обращение в суд или контролирующий орган",
    "LAWYER_CONTACT_REPORTED": "Обращение представителя или юриста",
    "DEMAND_AMOUNT_THRESHOLD_EXCEEDED": "Сумма требования превышает порог",
    "HIGH_DEMAND_AMOUNT": "Сумма требования достигла установленного порога",
    "HARM_REPORTED": "Заявлен вред здоровью",
    "LAWYER_OR_REPRESENTATIVE_CONTACT": "Обращение юриста или представителя",
    "OFFICIAL_REGULATOR_OR_COURT_SIGNAL": "Обращение в суд или контролирующий орган",
}
FACT_LABELS = {
    "PROBLEM_SUMMARY": "Описание ситуации",
    "SERVICE_TYPE": "Услуга",
    "SERVICE_DATE": "Дата услуги",
    "INCIDENT_DATE": "Дата проблемы",
    "CLAIM_DATE": "Дата обращения",
    "PATIENT_DEMAND": "Требование",
    "DEMAND_AMOUNT": "Сумма требования",
    "HOSPITALIZATION": "Госпитализация",
    "HARM_CLAIMED": "Заявленный вред",
    "RESPONSE_DEADLINE": "Срок ответа",
}


def history_callback(escalation_id: UUID, before: UUID) -> str:
    def encoded(value: UUID) -> str:
        return base64.urlsafe_b64encode(value.bytes).decode().rstrip("=")

    return f"esc:h:{encoded(escalation_id)}:{encoded(before)}"


def parse_history_callback(value: str) -> tuple[UUID, UUID]:
    prefix, action, escalation, before = value.split(":")
    if prefix != "esc" or action != "h" or len(escalation) != 22 or len(before) != 22:
        raise ValueError("invalid history cursor")
    return (
        UUID(bytes=base64.b64decode(escalation + "==", altchars=b"-_", validate=True)),
        UUID(bytes=base64.b64decode(before + "==", altchars=b"-_", validate=True)),
    )


def _chunks(blocks: list[str]) -> tuple[str, ...]:
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(current) + len(block) + 2 > 3900:
            if current:
                chunks.append(current)
            current = ""
        # Case facts can exceed one Telegram message; preserve them in the full card file.
        while len(block) > 3900:
            chunks.append(block[:3900])
            block = block[3900:]
        current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return tuple(chunks)


def discussion_messages(payload: dict[str, Any]) -> tuple[str, ...]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("invalid discussion page")
    labels = {"CLINIC_OWNER": "Владелец", "CLINIC_ADMIN": "Администратор", "CLINIC_LAWYER": "Юрист"}
    blocks = ["💬 ДИАЛОГ ПО КЕЙСУ — сообщения в хронологическом порядке"]
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("body"), str):
            raise ValueError("invalid discussion message")
        author = labels.get(item.get("authorRole"), "Участник клиники")
        timestamp = str(item.get("createdAt", ""))[:19].replace("T", " ")
        blocks.append(f"{author} · {timestamp}\n{item['body']}")
    if not items:
        blocks.append("Сообщений пока нет.")
    return _chunks(blocks)


def _fact_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def render_case_card(detail: dict[str, Any]) -> str:
    states = {"REQUIRED": "Ожидает юриста", "IN_PROGRESS": "В работе", "RESOLVED": "Завершён"}
    assigned = (
        "вы"
        if detail.get("assignedToMe")
        else "другой юрист"
        if detail.get("assignedMembershipId")
        else "не назначен"
    )
    lines = [
        f"⚖️ КЕЙС {detail.get('publicNumber', '')}",
        f"Риск: {detail.get('riskLevel', '')}",
        f"Статус: {states.get(detail.get('status'), 'Неизвестен')}",
        f"Ответственный: {assigned}",
        "",
        "Причины передачи:",
    ]
    lines.extend(f"• {REASONS.get(code, code)}" for code in detail.get("reasonCodes", []))
    facts = detail.get("facts", {})
    if isinstance(facts, dict):
        lines.extend(["", "Обстоятельства (полностью — в карточке ниже):"])
        for key, value in facts.items():
            if len("\n".join(lines)) > 2300:
                lines.append("… остальные обстоятельства — в полной карточке.")
                break
            lines.append(f"{FACT_LABELS.get(key, key)}: {_fact_text(value)[:400]}")
    lines.extend(
        [
            "",
            "Канонический отчёт Legal Core доступен в полной карточке. "
            "Статус готовности юридического анализа указан в самом отчёте."
            if detail.get("report")
            else "Проверенный отчёт ещё не сформирован; юрист может уточнять ситуацию.",
            "Внутреннее обсуждение клиники. Автоотправка пациенту отключена.",
        ]
    )
    return "\n".join(lines)[:3900]


def workspace_keyboard(
    escalation_id: UUID, detail: dict[str, Any], *, can_manage: bool
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "📋 Полная карточка и отчёт", callback_data=f"esc:card:{escalation_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 Последние сообщения", callback_data=f"case:escalation:{escalation_id}"
            )
        ],
    ]
    report = detail.get("report")
    if isinstance(report, dict) and report.get("reportId"):
        rows.insert(
            1, [InlineKeyboardButton("📄 Отчёт PDF", callback_data=f"esc:pdf:{escalation_id}")]
        )
    if can_manage and detail.get("status") != "RESOLVED":
        if detail.get("assignedToMe"):
            rows.append(
                [
                    InlineKeyboardButton(
                        "✅ Завершить обращение", callback_data=f"esc:resolve:{escalation_id}"
                    )
                ]
            )
        elif not detail.get("assignedMembershipId"):
            rows.append(
                [
                    InlineKeyboardButton(
                        "🙋 Взять в работу", callback_data=f"esc:claim:{escalation_id}"
                    )
                ]
            )
    rows.append([InlineKeyboardButton("← К списку кейсов", callback_data="case:escalations")])
    rows.extend(back_keyboard().inline_keyboard)
    return InlineKeyboardMarkup(rows)


async def show_workspace(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    escalation_id: UUID,
    *,
    before: UUID | None = None,
) -> None:
    actor = gateway_bot._actor_id(update)
    if actor is None:
        raise ValueError("missing actor")
    client = gateway_bot._legal_core(context)
    detail = await client.get_escalation(escalation_id, actor)
    membership = await client.get_actor(actor)
    history = await client.get_escalation_discussion(escalation_id, actor, before=before, limit=5)
    data = gateway_bot._user_data(context)
    gateway_bot._clear_pending_admin_grant(context)
    data.pop("quick_intake_pending", None)
    data.pop("quick_intake_candidate", None)
    if detail.get("status") != "RESOLVED":
        data[DISCUSSION_KEY] = str(escalation_id)
    await gateway_bot._reply(update, render_case_card(detail))
    for text in discussion_messages(history):
        await gateway_bot._reply(update, text)
    keyboard = workspace_keyboard(
        escalation_id,
        detail,
        can_manage=membership.get("role") in {"CLINIC_OWNER", "CLINIC_LAWYER"},
    )
    rows = list(keyboard.inline_keyboard)
    if history.get("nextBefore"):
        rows.insert(
            0,
            (
                InlineKeyboardButton(
                    "↑ Более ранние сообщения",
                    callback_data=history_callback(escalation_id, UUID(history["nextBefore"])),
                ),
            ),
        )
    instruction = (
        "Обращение завершено. История доступна для чтения."
        if detail.get("status") == "RESOLVED"
        else "Напишите обезличенный вопрос или ответ. Он будет сохранён именно в этом кейсе. "
        "Не указывайте ФИО, контакты и данные медицинских документов."
    )
    await gateway_bot._reply(update, instruction, reply_markup=InlineKeyboardMarkup(rows))


async def workspace_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    actor = gateway_bot._actor_id(update)
    if query is None or actor is None or not isinstance(query.data, str):
        raise ApplicationHandlerStop
    await query.answer()
    if gateway_bot.WIZARD_DATA_KEY in gateway_bot._user_data(context):
        await gateway_bot._reply(
            update,
            "Сначала вернитесь в главное меню; черновик сохранён.",
            reply_markup=back_keyboard(),
        )
        raise ApplicationHandlerStop
    gateway_bot._clear_pending_admin_grant(context)
    try:
        client = gateway_bot._legal_core(context)
        if query.data.startswith("esc:q:"):
            from telegram_gateway.analysis_runtime import _show_escalation_queue

            _, _, status, cursor = query.data.split(":")
            if status not in {"OPEN", "RESOLVED"}:
                raise ValueError("invalid queue status")
            await _show_escalation_queue(
                update, context, status=status, before=None if cursor == "first" else UUID(cursor)
            )
        elif query.data.startswith("esc:h:"):
            escalation_id, before = parse_history_callback(query.data)
            await show_workspace(update, context, escalation_id, before=before)
        else:
            _, action, raw_id = query.data.split(":")
            escalation_id = UUID(raw_id)
            if action == "claim":
                await client.claim_escalation(escalation_id, actor)
                await show_workspace(update, context, escalation_id)
            elif action == "resolve":
                detail = await client.get_escalation(escalation_id, actor)
                if not detail.get("assignedToMe") or detail.get("status") != "IN_PROGRESS":
                    await gateway_bot._reply(
                        update, "Завершить кейс может только ответственный юрист."
                    )
                else:
                    gateway_bot._clear_pending_admin_grant(context)
                    data = gateway_bot._user_data(context)
                    data.pop("quick_intake_pending", None)
                    data.pop("quick_intake_candidate", None)
                    data[DISCUSSION_KEY] = str(escalation_id)
                    data[RESOLUTION_KEY] = str(escalation_id)
                    await gateway_bot._reply(
                        update,
                        f"Кейс {detail.get('publicNumber', '')}. "
                        "Напишите итог проверки (1–1500 символов). "
                        "Он сохранится в истории, и обращение будет завершено.",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "← Отмена, к кейсу",
                                        callback_data=f"case:escalation:{escalation_id}",
                                    )
                                ],
                                *back_keyboard().inline_keyboard,
                            ]
                        ),
                    )
            elif action in {"card", "pdf"}:
                detail = await client.get_escalation(escalation_id, actor)
                if action == "pdf":
                    report = detail.get("report")
                    if not isinstance(report, dict):
                        raise ValueError("report not available")
                    pdf = await client.download_pdf(UUID(str(report.get("reportId"))), actor)
                    if update.effective_message is not None:
                        await update.effective_message.reply_document(
                            document=InputFile(BytesIO(pdf), filename=f"case-{escalation_id}.pdf"),
                            caption="Канонический отчёт Legal Core.",
                            reply_markup=InlineKeyboardMarkup(
                                [
                                    [
                                        InlineKeyboardButton(
                                            "← К кейсу",
                                            callback_data=f"case:escalation:{escalation_id}",
                                        )
                                    ]
                                ]
                            ),
                        )
                    raise ApplicationHandlerStop
                text = render_case_card(detail) + "\n\nВСЕ ОБСТОЯТЕЛЬСТВА:\n"
                text += json.dumps(detail.get("facts", {}), ensure_ascii=False, indent=2)
                text += "\n\nКАНОНИЧЕСКИЙ ОТЧЁТ LEGAL CORE:\n"
                text += json.dumps(detail.get("report"), ensure_ascii=False, indent=2)
                message = update.effective_message
                if message is not None:
                    await message.reply_document(
                        document=InputFile(
                            BytesIO(text.encode()), filename=f"case-{escalation_id}.txt"
                        ),
                        caption="Полная обезличенная карточка и канонический отчёт Legal Core.",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "← К кейсу",
                                        callback_data=f"case:escalation:{escalation_id}",
                                    )
                                ]
                            ]
                        ),
                    )
            else:
                raise ValueError("unknown action")
    except (LegalCoreApiError, ValueError) as exc:
        code = exc.code if isinstance(exc, LegalCoreApiError) else "INVALID_ACTION"
        messages = {
            "ESCALATION_ALREADY_ASSIGNED": "Кейс уже взят другим юристом. Обновите карточку.",
            "ESCALATION_UNAVAILABLE": "Кейс уже взят или завершён. Обновите карточку.",
            "ASSIGNEE_REQUIRED": "Завершить кейс может только ответственный юрист.",
            "ESCALATION_RESOLVED": "Обращение уже завершено.",
            "ESCALATION_NOT_FOUND": "Кейс недоступен для этого аккаунта.",
        }
        await gateway_bot._reply(
            update,
            messages.get(code, "Не удалось выполнить действие. Обновите карточку."),
            reply_markup=back_keyboard(),
        )
    raise ApplicationHandlerStop
