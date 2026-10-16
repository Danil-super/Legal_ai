# ruff: noqa: RUF001
"""Explicit owner-of-case correction intent with safe network retries."""

import re
from uuid import NAMESPACE_URL, UUID, uuid5

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.clarification_intent import (
    decode_clarification_callback,
    decode_preparation_callback,
    encode_clarification_callback,
)
from telegram_gateway.ui import back_keyboard


async def _reply_opened_draft(update: Update, draft_id: UUID) -> None:
    await gateway_bot._reply(update,
        "📝 Заполненный черновик для уточнения открыт. Старый кейс и отчёты не изменены. "
        "Исходные файлы остаются в старом кейсе; при необходимости приложите их заново. "
        "Новая карточка будет создана только после вашей проверки и подтверждения.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Продолжить уточнение", callback_data=f"case:draft:{draft_id}")],
            *back_keyboard().inline_keyboard,
        ]))


async def _create_preparation_clarification(
    update: Update, context: ContextTypes.DEFAULT_TYPE, actor: int, callback: str,
) -> None:
    try:
        case_id, operation_id = decode_preparation_callback(callback)
        message_id = getattr(getattr(update.callback_query, "message", None), "message_id", None)
        if type(message_id) is not int or message_id < 1:
            raise ValueError("invalid clarification message")
        client = gateway_bot._legal_core(context)
        preview = await client.clarification_preview(
            case_id, actor, source_operation_id=operation_id,
        )
        if not isinstance(preview, dict) or set(preview) != {
            "caseId", "expectedFactSnapshotSha256", "expectedOperationId",
            "expectedOperationSha256",
        }:
            raise ValueError("invalid preparation preview")
        fact_sha, operation_sha = (preview["expectedFactSnapshotSha256"],
                                   preview["expectedOperationSha256"])
        if (preview["caseId"] != str(case_id)
                or preview["expectedOperationId"] != str(operation_id)
                or not isinstance(fact_sha, str) or re.fullmatch(r"[0-9a-f]{64}", fact_sha) is None
                or not isinstance(operation_sha, str)
                or re.fullmatch(r"[0-9a-f]{64}", operation_sha) is None):
            raise ValueError("preparation preview origin mismatch")
        key = uuid5(NAMESPACE_URL,
                    f"clarification:v3:{actor}:{case_id}:{fact_sha}:{operation_id}:"
                    f"{operation_sha}:{message_id}")
        draft = await client.create_clarification_draft(
            case_id, actor, expected_sha256=fact_sha, key=key,
            expected_operation_id=operation_id, expected_operation_sha256=operation_sha,
        )
        if not isinstance(draft, dict):
            raise ValueError("invalid preparation draft")
        draft_id = UUID(str(draft["id"]))
        data = draft.get("draftData")
        if not isinstance(data, dict) or data.get("correctionSource") != {
            "caseId": str(case_id), "factSnapshotSha256": fact_sha,
            "operationId": str(operation_id), "operationSha256": operation_sha,
        }:
            raise ValueError("preparation draft origin mismatch")
        gateway_bot._saved_draft_selection(draft_id, draft)
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        code = exc.code if isinstance(exc, LegalCoreApiError) else None
        if code in {"INTAKE_DRAFT_EXPIRED", "CASE_CONTENT_EXPIRED", "CASE_CONTENT_PURGED"}:
            text = "⚠️ Срок хранения истёк. Создайте новый кейс из главного меню."
        elif code in {
            "CASE_PREPARATION_STALE", "CASE_FACTS_STALE", "CLARIFICATION_OPERATION_UNAVAILABLE",
            "CLARIFICATION_OPERATION_INVALID", "PREPARATION_RESULT_INVALID",
            "CLARIFICATION_RESULT_INVALID",
        }:
            text = ("⚠️ Этот результат изменился или недоступен для дополнения. "
                    "Откройте актуальную карточку кейса. Исходный кейс не изменён.")
        else:
            text = ("⚠️ Не удалось открыть уточнение. Исходный кейс не изменён. "
                    "Повторное нажатие безопасно.")
        await gateway_bot._reply(update, text, reply_markup=back_keyboard())
        raise ApplicationHandlerStop from None
    await _reply_opened_draft(update, draft_id)
    raise ApplicationHandlerStop


async def create_clarification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback = await gateway_bot._answer_callback(update)
    actor = gateway_bot._actor_id(update)
    if actor is None or not isinstance(callback, str):
        raise ApplicationHandlerStop
    if callback.startswith("case:clarify2:"):
        await _create_preparation_clarification(update, context, actor, callback)
        raise ApplicationHandlerStop
    try:
        legacy = re.fullmatch(r"case:clarify:[0-9a-f-]{36}", callback) is not None
        if legacy:
            case_id, source_report_id = UUID(callback.removeprefix("case:clarify:")), None
        else:
            case_id, source_report_id = decode_clarification_callback(callback)
        client = gateway_bot._legal_core(context)
        preview = await client.clarification_preview(case_id, actor,
                                                     source_report_id=source_report_id)
        if not isinstance(preview, dict):
            raise ValueError("invalid clarification preview")
        sha = preview.get("expectedFactSnapshotSha256")
        report_id_value = preview.get("expectedReportId")
        report_sha = preview.get("expectedReportSha256")
        if (preview.get("caseId") != str(case_id) or not isinstance(sha, str)
                or re.fullmatch(r"[0-9a-f]{64}", sha) is None
                or not isinstance(report_id_value, str) or not isinstance(report_sha, str)
                or re.fullmatch(r"[0-9a-f]{64}", report_sha) is None):
            raise ValueError("invalid clarification preview")
        report_id = UUID(report_id_value)
        if source_report_id is not None and report_id != source_report_id:
            raise ValueError("clarification preview report mismatch")
        if legacy:
            await gateway_bot._reply(
                update, "Эта кнопка уточнения устарела. Проверен последний сохранённый отчёт "
                f"кейса: {report_id}. Для него можно открыть новый заполненный черновик. "
                "Старый кейс и отчёты не изменены; исходные файлы не копируются.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Открыть уточнение этого отчёта",
                        callback_data=encode_clarification_callback(case_id, report_id))],
                    *back_keyboard().inline_keyboard,
                ]),
            )
            raise ApplicationHandlerStop
        query = update.callback_query
        message = getattr(query, "message", None)
        message_id = getattr(message, "message_id", None)
        if type(message_id) is not int or message_id < 1:
            raise ValueError("invalid clarification message")
        # One button on one message is one intent, including after gateway restart.
        key = uuid5(NAMESPACE_URL,
                    f"clarification:v2:{actor}:{case_id}:{sha}:{report_id}:{report_sha}:"
                    f"{message_id}")
        draft = await client.create_clarification_draft(
            case_id, actor, expected_sha256=sha, key=key,
            expected_report_id=report_id, expected_report_sha256=report_sha,
        )
        if not isinstance(draft, dict):
            raise ValueError("invalid clarification draft")
        draft_id = UUID(str(draft["id"]))
        data = draft.get("draftData")
        if (not isinstance(data, dict) or data.get("correctionSource") != {
                "caseId": str(case_id), "factSnapshotSha256": sha,
                "reportId": str(report_id), "reportSha256": report_sha,
        }):
            raise ValueError("invalid clarification draft")
        gateway_bot._saved_draft_selection(draft_id, draft)
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        if isinstance(exc, LegalCoreApiError) and exc.code == "CLARIFICATION_SCHEMA_UNSUPPORTED":
            await gateway_bot._reply(
                update, "Этот кейс создан по старой анкете. Для уточнения создайте новый "
                "кейс по общей пошаговой анкете. Старый кейс и его отчёты останутся без изменений.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "Создать кейс по новой анкете", callback_data="case:start",
                    )],
                    *back_keyboard().inline_keyboard,
                ]),
            )
            raise ApplicationHandlerStop from None
        if isinstance(exc, LegalCoreApiError) and exc.code in {
            "CASE_REPORT_STALE", "CLARIFICATION_REPORT_BINDING_REQUIRED",
            "CLARIFICATION_REPORT_INVALID",
        }:
            await gateway_bot._reply(
                update, "⚠️ Не удалось подтвердить исходный отчёт для уточнения. "
                "Откройте последний сохранённый отчёт кейса и начните уточнение оттуда. "
                "Старый кейс и отчёты не изменены.", reply_markup=back_keyboard(),
            )
            raise ApplicationHandlerStop from None
        text = (
            "⚠️ Срок хранения этого черновика истёк. Создайте новый кейс из главного меню. "
            "Старый кейс не изменён."
            if isinstance(exc, LegalCoreApiError) and exc.code == "INTAKE_DRAFT_EXPIRED" else
            "⚠️ Не удалось открыть уточнение. Старый кейс сохранён. Повторное нажатие безопасно."
        )
        await gateway_bot._reply(update, text,
            reply_markup=back_keyboard())
        raise ApplicationHandlerStop from None
    await _reply_opened_draft(update, draft_id)
    raise ApplicationHandlerStop
