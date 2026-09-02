# ruff: noqa: RUF001
"""Explicit one-message intake mode that reuses the existing durable Telegram wizard.

Quick intake is local and deterministic: it does not call an LLM and does not make legal
conclusions. Candidate extraction is reviewed by the administrator, then only a safe contiguous
prefix is persisted into an ordinary Legal Core intake draft. Continuation uses the existing
``case:draft:<uuid>`` ConversationHandler entry point.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from uuid import UUID

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError, LegalCoreClient
from telegram_gateway.clinic_document_library_runtime import (
    build_application_with_clinic_document_library,
)
from telegram_gateway.quick_intake import (
    QuickIntakeError,
    QuickIntakePrivacyError,
    QuickIntakeResult,
    extract_quick_intake,
)

logger = logging.getLogger(__name__)
_QUICK_PENDING_KEY = "quick_intake_pending"
_QUICK_CANDIDATE_KEY = "quick_intake_candidate"
_CLINIC_UPLOAD_KEY = "clinic_document_upload"
_MAX_SUMMARY = 3900

_INCIDENT_LABELS = {
    "QUALITY_COMPLAINT": "качество лечения",
    "PAYMENT_DISPUTE": "оплата / возврат",
    "INFORMED_CONSENT": "согласие / документы",
    "PERSONAL_DATA": "персональные данные",
    "OTHER": "другая ситуация",
}
_DEMAND_LABELS = {
    "NO_SPECIFIC_DEMAND": "конкретных требований пока нет",
    "REWORK_DEMAND": "повторное лечение / переделка",
    "REFUND_DEMAND": "возврат денег",
    "COMPENSATION_DEMAND": "компенсация",
}
_FIELD_LABELS = {
    "incident_type": "тип ситуации",
    "service_type": "услуга",
    "service_date": "дата услуги",
    "incident_date": "дата проблемы",
    "claim_date": "дата обращения пациента",
    "problem_summary": "описание",
    "patient_demand": "требование",
    "demand_amount_kopecks": "сумма",
    "formal_claim": "письменная претензия",
    "harm_claimed": "заявление о вреде здоровью",
    "hospitalization": "госпитализация",
    "lawyer_contact": "обращение юриста/представителя",
    "regulator_or_court": "фактическое обращение в орган/суд",
    "regulator_threat": "угроза обращения в орган/суд",
    "documents_status": "основные документы",
}
_SIGNAL_LABELS = {"YES": "да", "NO": "нет", "UNKNOWN": "неизвестно"}
_DOCUMENT_LABELS = {"COMPLETE": "есть", "PARTIAL": "есть не всё", "NONE": "нет"}


def _user_data(context: ContextTypes.DEFAULT_TYPE) -> dict[Any, Any]:
    data = context.user_data
    if data is None:
        raise RuntimeError("Telegram user storage is unavailable")
    return data


def _legal_core(context: ContextTypes.DEFAULT_TYPE) -> LegalCoreClient:
    client = context.bot_data.get(gateway_bot.LEGAL_CORE_CLIENT_KEY)
    if client is None:
        raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "Legal Core unavailable")
    return cast(LegalCoreClient, client)


def _actor_id(update: Update) -> int | None:
    user = update.effective_user
    return None if user is None else user.id


def _clear_quick(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = _user_data(context)
    data.pop(_QUICK_PENDING_KEY, None)
    data.pop(_QUICK_CANDIDATE_KEY, None)


def _quick_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Создать черновик", callback_data="quick:accept"),
                InlineKeyboardButton("📝 Заполнить вручную", callback_data="quick:manual"),
            ],
            [InlineKeyboardButton("❌ Отменить", callback_data="quick:cancel")],
        ]
    )


def _continue_keyboard(draft_id: UUID) -> InlineKeyboardMarkup:
    callback = f"case:draft:{draft_id}"
    if len(callback.encode()) > 64:
        raise ValueError("quick intake draft callback is too long")
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("▶️ Продолжить уточнение", callback_data=callback)]]
    )


def _manual_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📝 Создать кейс вручную", callback_data="case:start")]]
    )


def _date_label(value: object) -> str:
    if isinstance(value, dict) and isinstance(value.get("date"), str):
        return str(value["date"])
    return "не распознано"


def _candidate_value(field: str, value: object) -> str:
    if field == "incident_type" and isinstance(value, str):
        return _INCIDENT_LABELS.get(value, value)
    if field == "patient_demand" and isinstance(value, str):
        return _DEMAND_LABELS.get(value, value)
    if field in {"service_date", "incident_date", "claim_date"}:
        return _date_label(value)
    if field == "demand_amount_kopecks" and isinstance(value, int):
        return f"{value / 100:,.2f} ₽".replace(",", " ")
    if field in {
        "formal_claim",
        "harm_claimed",
        "hospitalization",
        "lawyer_contact",
        "regulator_or_court",
        "regulator_threat",
    } and isinstance(value, str):
        return _SIGNAL_LABELS.get(value, value)
    if field == "documents_status" and isinstance(value, str):
        return _DOCUMENT_LABELS.get(value, value)
    if field == "problem_summary" and isinstance(value, str):
        compact = " ".join(value.split())
        return compact[:300] + ("…" if len(compact) > 300 else "")
    return str(value)[:300]


def render_quick_candidate(result: QuickIntakeResult) -> str:
    lines = [
        "🧩 Предварительно распознано",
        "",
        "Это НЕ юридический анализ. Бот только выделил кандидаты фактов локально, без LLM.",
        "",
    ]
    ordered_fields = (
        "incident_type",
        "service_type",
        "service_date",
        "incident_date",
        "claim_date",
        "problem_summary",
        "patient_demand",
        "demand_amount_kopecks",
        "formal_claim",
        "harm_claimed",
        "hospitalization",
        "lawyer_contact",
        "regulator_or_court",
        "regulator_threat",
        "documents_status",
    )
    for field in ordered_fields:
        if field not in result.candidate_data:
            continue
        label = _FIELD_LABELS[field]
        lines.append(f"• {label}: {_candidate_value(field, result.candidate_data[field])}")

    lines.extend(
        [
            "",
            f"Следующий обязательный шаг: {result.next_wizard_state}",
            f"Автоматически сохраняемых полей: {len(result.draft_data)}",
        ]
    )
    if result.dropped_candidate_fields:
        lines.append(
            "Остальные распознанные признаки будут повторно подтверждены обычным wizard, "
            "а не приняты молча."
        )
    if any(result.redaction_counts.values()):
        lines.append("Прямые идентификаторы (телефон/e-mail/ID) были локально заменены.")
    lines.extend(
        [
            "",
            "Проверьте распознанное. После создания черновика бот продолжит с первого "
            "недостающего вопроса.",
        ]
    )
    rendered = "\n".join(lines)
    return rendered if len(rendered) <= _MAX_SUMMARY else rendered[:3850] + "\n…"


def _serialize_candidate(result: QuickIntakeResult) -> dict[str, object]:
    return {
        "draft_data": result.draft_data,
        "next_wizard_state": result.next_wizard_state,
        "candidate_data": result.candidate_data,
        "dropped_candidate_fields": list(result.dropped_candidate_fields),
    }


def _stored_candidate(context: ContextTypes.DEFAULT_TYPE) -> dict[str, object] | None:
    raw = _user_data(context).get(_QUICK_CANDIDATE_KEY)
    return raw if isinstance(raw, dict) else None


async def start_quick_intake(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    actor_id = _actor_id(update)
    if actor_id is None:
        await gateway_bot._reply(update, "Не удалось определить администратора.")
        return
    user_data = _user_data(context)
    if gateway_bot.WIZARD_DATA_KEY in user_data:
        await gateway_bot._reply(
            update,
            "Сначала завершите текущий кейс или вернитесь в меню — его черновик уже сохраняется.",
        )
        return
    if _CLINIC_UPLOAD_KEY in user_data:
        await gateway_bot._reply(
            update,
            "Сначала завершите или отмените загрузку документа клиники командой /cancel_upload.",
        )
        return
    try:
        actor = await _legal_core(context).get_actor(actor_id)
        if actor.get("role") != "CLINIC_ADMIN":
            raise ValueError("unexpected actor role")
    except LegalCoreApiError as exc:
        logger.warning("quick intake actor authorization failed: %s", exc.code)
        if exc.code == "SUBSCRIPTION_INACTIVE":
            await gateway_bot._reply(update, "🔒 Доступ по подписке сейчас не активен.")
        elif exc.status_code == 403:
            await gateway_bot._reply(update, "🔒 Аккаунт не подключён как администратор клиники.")
        else:
            await gateway_bot._reply(update, "⚠️ Legal Core временно недоступен.")
        return
    except ValueError:
        await gateway_bot._reply(update, "⚠️ Legal Core вернул некорректный ответ.")
        return

    _clear_quick(context)
    _user_data(context)[_QUICK_PENDING_KEY] = True
    await gateway_bot._reply(
        update,
        "✍️ Опишите ситуацию ОДНИМ сообщением (10–1500 символов).\n\n"
        "Лучше указать: услугу, даты лечения/проблемы/обращения, требование и сумму, "
        "есть ли письменная претензия, вред здоровью, юрист/представитель, обращение или "
        "угроза обращения в суд/Роспотребнадзор/Росздравнадзор.\n\n"
        "Не пишите ФИО, адрес, номер карты или другие персональные данные. Телефон/e-mail/ID "
        "будут локально удалены. На этом шаге никакого юридического ответа не формируется."
    )


async def start_quick_intake_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None or query.data != "quick:start":
        raise ApplicationHandlerStop
    await query.answer()
    await start_quick_intake(update, context)
    raise ApplicationHandlerStop


async def cancel_quick_intake(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    _clear_quick(context)
    await gateway_bot._reply(update, "Быстрое описание кейса отменено.")


async def receive_quick_description(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user_data = _user_data(context)
    if user_data.get(_QUICK_PENDING_KEY) is not True:
        return
    message = update.effective_message
    if message is None or not isinstance(message.text, str):
        return
    try:
        result = extract_quick_intake(message.text)
    except QuickIntakePrivacyError as exc:
        await gateway_bot._reply(
            update,
            "⚠️ Похоже, в тексте есть ФИО. Удалите имена пациента/врача/представителя "
            "и отправьте описание ещё раз. Режим быстрого ввода остаётся включён.",
        )
        raise ApplicationHandlerStop from exc
    except QuickIntakeError as exc:
        await gateway_bot._reply(
            update,
            "Описание должно содержать 10–1500 символов. Отправьте одно нейтральное "
            "обезличенное сообщение.",
        )
        raise ApplicationHandlerStop from exc

    user_data.pop(_QUICK_PENDING_KEY, None)
    user_data[_QUICK_CANDIDATE_KEY] = _serialize_candidate(result)
    await message.reply_text(render_quick_candidate(result), reply_markup=_quick_keyboard())
    raise ApplicationHandlerStop


async def quick_candidate_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    actor_id = _actor_id(update)
    if query is None or actor_id is None or not isinstance(query.data, str):
        raise ApplicationHandlerStop
    await query.answer()

    if query.data == "quick:cancel":
        _clear_quick(context)
        await gateway_bot._reply(update, "Быстрое описание отменено.")
        raise ApplicationHandlerStop
    if query.data == "quick:manual":
        _clear_quick(context)
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                "Откройте обычный безопасный wizard:",
                reply_markup=_manual_keyboard(),
            )
        raise ApplicationHandlerStop
    if query.data != "quick:accept":
        raise ApplicationHandlerStop

    stored = _stored_candidate(context)
    if stored is None:
        await gateway_bot._reply(
            update,
            "⚠️ Предварительное описание уже истекло. Запустите /describe_case ещё раз.",
        )
        raise ApplicationHandlerStop
    draft_data = stored.get("draft_data")
    next_state = stored.get("next_wizard_state")
    if not isinstance(draft_data, dict) or not isinstance(next_state, str):
        _clear_quick(context)
        await gateway_bot._reply(update, "⚠️ Черновик быстрого ввода повреждён. Начните заново.")
        raise ApplicationHandlerStop

    client = _legal_core(context)
    created_id: UUID | None = None
    created_revision: int | None = None
    try:
        created = await client.create_intake_draft(actor_id)
        created_id = UUID(str(created["id"]))
        created_revision = int(created["revision"])
        if created.get("wizardState") != "INCIDENT" or created_revision < 1:
            raise ValueError("draft create response is invalid")

        if draft_data or next_state != "INCIDENT":
            saved = await client.save_intake_draft(
                created_id,
                actor_id,
                expected_revision=created_revision,
                wizard_state=next_state,
                draft_data=draft_data,
            )
            saved_revision = saved.get("revision")
            if (
                saved.get("wizardState") != next_state
                or not isinstance(saved_revision, int)
                or saved_revision <= created_revision
            ):
                raise ValueError("draft save response is invalid")
            created_revision = saved_revision
    except (KeyError, TypeError, ValueError, LegalCoreApiError) as exc:
        logger.warning("quick intake durable draft creation failed: %s", type(exc).__name__)
        if created_id is not None and created_revision is not None:
            try:
                await client.archive_intake_draft(
                    created_id,
                    actor_id,
                    expected_revision=created_revision,
                )
            except (ValueError, LegalCoreApiError):
                logger.warning("quick intake orphan draft cleanup failed")
        await gateway_bot._reply(
            update,
            "⚠️ Не удалось сохранить быстрый черновик. Попробуйте /describe_case ещё раз.",
        )
        raise ApplicationHandlerStop from exc

    _clear_quick(context)
    message = update.effective_message
    if message is not None and created_id is not None:
        await message.reply_text(
            "✅ Черновик сохранён в Legal Core. Нажмите ниже: обычный wizard продолжит "
            "с первого недостающего шага и повторно подтвердит всё неоднозначное.",
            reply_markup=_continue_keyboard(created_id),
        )
    raise ApplicationHandlerStop


def build_application_with_quick_intake(token: str) -> gateway_bot.TelegramApplication:
    application = build_application_with_clinic_document_library(token)
    application.add_handler(CommandHandler("describe_case", start_quick_intake), group=-3)
    application.add_handler(CommandHandler("cancel_quick", cancel_quick_intake), group=-3)
    application.add_handler(
        CallbackQueryHandler(
            quick_candidate_callback,
            pattern=r"^quick:(accept|manual|cancel)$",
        ),
        group=-3,
    )
    application.add_handler(
        CallbackQueryHandler(start_quick_intake_callback, pattern=r"^quick:start$"),
        group=-3,
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_quick_description),
        group=-3,
    )
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    application = build_application_with_quick_intake(gateway_bot.load_token())
    application.run_polling(
        allowed_updates=gateway_bot.ALLOWED_UPDATES,
        bootstrap_retries=3,
        drop_pending_updates=False,
    )
