# ruff: noqa: RUF001
"""Personal case history and authorized, queued downloads of saved report versions."""

import re
from datetime import datetime
from uuid import UUID

from legal_core.case_history_contracts import PAGE_SIZE, CaseHistoryDetail, CaseHistoryPage
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.clarification_intent import (
    decode_clarification_callback,
    encode_clarification_callback,
)
from telegram_gateway.report_pdf_delivery import queue_report_pdf
from telegram_gateway.ui import back_keyboard

_STATUSES = {
    "COLLECTING": "Карточка", "NEEDS_INFORMATION": "Нужны уточнения",
    "INTAKE_COMPLETE": "Кейс сохранён", "READY_FOR_ANALYSIS": "Ожидает анализа",
    "ANALYZING": "Готовится отчёт", "ANALYSIS_BLOCKED": "Нужны уточнения",
    "REPORT_READY": "Отчёт готов", "ESCALATION_REQUIRED": "На проверке юриста",
    "CLOSED": "Закрыт", "CANCELLED": "Отменён", "CONTENT_PURGED": "Срок хранения истёк",
}


def _date(value: datetime) -> str:
    return value.strftime("%d.%m.%Y")


def _pdf_callback(case_id: UUID, report_id: UUID, offset: int) -> str:
    pair = encode_clarification_callback(case_id, report_id).removeprefix("case:clarify:")
    return f"history:pdf:{pair}:{offset}"


async def _detail(context: ContextTypes.DEFAULT_TYPE, actor: int,
                  case_id: UUID, offset: int) -> CaseHistoryDetail:
    raw = await bot._legal_core(context)._json_request(
        "GET", f"/v1/case-history/{case_id}?offset={offset}", telegram_user_id=actor,
    )
    detail = CaseHistoryDetail.model_validate(raw)
    if detail.case.id != case_id:
        raise ValueError("history case mismatch")
    return detail


async def _list(update: Update, context: ContextTypes.DEFAULT_TYPE,
                actor: int, offset: int) -> None:
    page = CaseHistoryPage.model_validate(await bot._legal_core(context)._json_request(
        "GET", f"/v1/case-history?offset={offset}", telegram_user_id=actor,
    ))
    rows = [[InlineKeyboardButton(
        f"{case.public_number} · {_STATUSES[case.status]} · {_date(case.created_at)}",
        callback_data=f"history:view:{case.id}:0",
    )] for case in page.items]
    nav = []
    if offset:
        nav.append(InlineKeyboardButton(
            "← Назад", callback_data=f"history:list:{max(0, offset-PAGE_SIZE)}",
        ))
    if page.next_offset is not None:
        nav.append(InlineKeyboardButton(
            "Далее →", callback_data=f"history:list:{page.next_offset}",
        ))
    if nav:
        rows.append(nav)
    rows.extend(list(row) for row in back_keyboard().inline_keyboard)
    text = "📂 Мои кейсы\nВыберите кейс, чтобы открыть его отчёты." if page.items else (
        "📂 Мои кейсы\nСохранённых кейсов пока нет."
    )
    if not page.items:
        rows.insert(0, [InlineKeyboardButton("📝 Создать кейс", callback_data="quick:start")])
    await bot._reply(update, text, reply_markup=InlineKeyboardMarkup(rows))


async def _view(update: Update, context: ContextTypes.DEFAULT_TYPE,
                actor: int, case_id: UUID, offset: int) -> None:
    detail = await _detail(context, actor, case_id, offset)
    rows = [[InlineKeyboardButton(
        f"{'📄 Отчёт' if report.kind == 'READY' else '📎 Карточка без рекомендаций'} "
        f"· {_date(report.created_at)} · версия {report.report_version}",
        callback_data=_pdf_callback(case_id, report.id, offset),
    )] for report in detail.reports]
    if detail.escalation_id is not None:
        rows.append([InlineKeyboardButton(
            "⚖️ Работа с юристом", callback_data=f"case:escalation:{detail.escalation_id}",
        )])
    elif not detail.reports:
        rows.append([InlineKeyboardButton(
            "Подготовить отчёт", callback_data=f"case:analyze:{case_id}",
        )])
    if offset:
        rows.append([InlineKeyboardButton(
            "← Новые отчёты", callback_data=f"history:view:{case_id}:{max(0,offset-PAGE_SIZE)}",
        )])
    if detail.next_offset is not None:
        rows.append([InlineKeyboardButton(
            "Предыдущие отчёты →", callback_data=f"history:view:{case_id}:{detail.next_offset}",
        )])
    rows.append([InlineKeyboardButton("← Мои кейсы", callback_data="history:list:0")])
    text = (f"📋 Кейс {detail.case.public_number}\n"
            f"{_STATUSES[detail.case.status]} · {_date(detail.case.created_at)}\n\n"
            + ("Выберите документ ниже." if detail.reports else "Готового отчёта пока нет."))
    await bot._reply(update, text, reply_markup=InlineKeyboardMarkup(rows))


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = bot._actor_id(update)
    if actor is None:
        return
    try:
        await _list(update, context, actor, 0)
    except (LegalCoreApiError, ValueError):
        await bot._reply(update, "Не удалось открыть ваши кейсы. Попробуйте ещё раз.",
                         reply_markup=back_keyboard())


async def history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback = await bot._answer_callback(update)
    actor = bot._actor_id(update)
    if callback is None or actor is None:
        raise ApplicationHandlerStop
    try:
        parts = callback.split(":")
        action, offset = parts[1], int(parts[-1])
        if not 0 <= offset <= 100_000:
            raise ValueError("invalid history page")
        if action == "list":
            await _list(update, context, actor, offset)
        elif action == "view":
            await _view(update, context, actor, UUID(parts[2]), offset)
        else:
            case_id, report_id = decode_clarification_callback("case:clarify:" + parts[2])

            async def resolve_report() -> UUID:
                # Re-read inside the bounded file queue: a queued click is not authorization.
                detail = await _detail(context, actor, case_id, offset)
                if not any(report.id == report_id for report in detail.reports):
                    raise ValueError("report is not in this case page")
                return report_id

            await queue_report_pdf(context, actor_id=actor, resolve_report=resolve_report,
                                   return_callback=f"history:view:{case_id}:{offset}")
    except (LegalCoreApiError, ValueError, IndexError):
        await bot._reply(update, "Кейс или отчёт недоступен. Откройте список заново.",
                         reply_markup=InlineKeyboardMarkup([
                             [InlineKeyboardButton("📂 Мои кейсы", callback_data="history:list:0")],
                         ]))
    raise ApplicationHandlerStop


def install(application: bot.TelegramApplication) -> None:
    application.add_handler(CommandHandler("cases", history_command), group=-1)
    application.add_handler(CallbackQueryHandler(history_callback, pattern=re.compile(
        r"^history:(?:list:[0-9]{1,6}|view:[0-9a-f-]{36}:[0-9]{1,6}|"
        r"pdf:[A-Za-z0-9_-]{43}:[0-9]{1,6})$",
    )), group=-1)
