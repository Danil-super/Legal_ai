# ruff: noqa: RUF001
"""Safety-scoped Telegram polling gateway."""

import logging
import os
import re
from collections.abc import Mapping
from enum import IntEnum
from io import BytesIO
from pathlib import Path
from typing import Any, TypeAlias, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx2
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from telegram_gateway.case_wizard import (
    CaseDraft,
    DateFactValue,
    LegalCoreApiError,
    LegalCoreClient,
    SignalAnswer,
    facts_from_draft,
    parse_date_answer,
    parse_ruble_amount_to_kopecks,
    telegram_summary_from_report,
)
from telegram_gateway.ui import (
    HELP_MESSAGE,
    MAIN_MENU_CALLBACKS,
    SCREENS,
    START_MESSAGE,
    TEXT_INPUT_DISABLED_MESSAGE,
    WELCOME_IMAGE,
    back_keyboard,
    main_menu_keyboard,
)

logger = logging.getLogger(__name__)

TelegramApplication: TypeAlias = Application[Any, Any, Any, Any, Any, Any]
TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
READY_FILE = Path("/tmp/telegram-gateway-ready")
ALLOWED_UPDATES = ["message", "callback_query"]
LEGAL_CORE_CLIENT_KEY = "legal_core_client"
WIZARD_DATA_KEY = "case_wizard"
LEGAL_CORE_TIMEOUT_SECONDS = 15.0


class WizardState(IntEnum):
    INCIDENT = 1
    SERVICE_TYPE = 2
    SERVICE_DATE = 3
    INCIDENT_DATE = 4
    CLAIM_DATE = 5
    PROBLEM_SUMMARY = 6
    PATIENT_DEMAND = 7
    DEMAND_AMOUNT = 8
    FORMAL_CLAIM = 9
    CLAIM_RECEIVED_AT = 10
    CLAIM_DEADLINE = 11
    HARM = 12
    HOSPITALIZATION = 13
    AUTHORITY = 14
    AUTHORITY_KIND = 15
    AUTHORITY_DATE = 16
    AUTHORITY_DEADLINE = 17
    DOCUMENTS = 18
    CONFIRM = 19
    LAWYER = 20
    REPRESENTATIVE_AUTHORITY = 21
    LAWYER_DEADLINE = 22
    REGULATOR_THREAT = 23


def load_token(environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    token = source.get("TELEGRAM_BOT_TOKEN", "").strip()
    if len(token) < 20 or TOKEN_PATTERN.fullmatch(token) is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing or malformed")
    return token


def load_legal_core_url(environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    value = source.get("LEGAL_CORE_URL", "http://legal-core:8000").strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("LEGAL_CORE_URL is malformed")
    return value


async def _reply(update: Update, text: str) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    del context
    message = update.effective_message
    if message is not None:
        # Path uploads and inline keyboards follow the official PTB 22.8 APIs.
        # Sources: https://docs.python-telegram-bot.org/en/v22.8/telegram.bot.html
        # https://docs.python-telegram-bot.org/en/v22.8/telegram.inlinekeyboardbutton.html
        await message.reply_photo(
            photo=WELCOME_IMAGE,
            caption=START_MESSAGE,
            reply_markup=main_menu_keyboard(),
        )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    await start(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    del context
    await _reply(update, HELP_MESSAGE)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    del context
    user = update.effective_user
    if user is None:
        await _reply(update, "Не удалось определить Telegram ID.")
        return
    await _reply(update, f"👤 Ваш Telegram ID: {user.id}")


async def grant_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    owner_id = _actor_id(update)
    arguments = context.args
    if owner_id is None:
        await _reply(update, "Не удалось определить владельца.")
        return
    if len(arguments) != 1 or not arguments[0].isdigit():
        await _reply(update, "Использование: /grant_access <Telegram_ID>")
        return
    target_id = int(arguments[0])
    if not 0 < target_id <= 9_223_372_036_854_775_807:
        await _reply(update, "Telegram ID указан некорректно.")
        return
    try:
        granted = await _legal_core(context).grant_subscription(owner_id, target_id)
    except LegalCoreApiError as exc:
        if exc.code == "OWNER_REQUIRED":
            await _reply(update, "🔒 Эта команда доступна только владельцу сервиса.")
        elif exc.code == "TARGET_ADMIN_AMBIGUOUS":
            await _reply(
                update,
                "⚠️ Для этого ID уже задано несколько клиник. Обратитесь в поддержку.",
            )
        else:
            await _reply(update, "⚠️ Не удалось выдать доступ. Попробуйте позже.")
        return
    if granted.get("telegramUserId") != target_id or granted.get("status") != "ACTIVE":
        await _reply(update, "⚠️ Legal Core вернул некорректный ответ. Попробуйте позже.")
        return
    await _reply(
        update,
        f"✅ Доступ выдан для Telegram ID {target_id}. "
        "Попросите пользователя открыть /start и затем /menu.",
    )


def _keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
    )


INCIDENT_KEYBOARD = _keyboard(
    [
        [("🦷 Качество лечения", "case:incident:QUALITY_COMPLAINT")],
        [("💳 Оплата / возврат", "case:incident:PAYMENT_DISPUTE")],
        [("📄 Согласие и документы", "case:incident:INFORMED_CONSENT")],
        [("🔐 Персональные данные", "case:incident:PERSONAL_DATA")],
        [("❓ Другая ситуация", "case:incident:OTHER")],
    ]
)
DEMAND_KEYBOARD = _keyboard(
    [
        [("Требований пока нет", "case:demand:NO_SPECIFIC_DEMAND")],
        [("Повторное лечение", "case:demand:REWORK_DEMAND")],
        [("Возврат денег", "case:demand:REFUND_DEMAND")],
        [("Компенсация", "case:demand:COMPENSATION_DEMAND")],
    ]
)
YES_NO_KEYBOARDS = {
    name: _keyboard(
        [
            [
                ("✅ Да", f"case:{name}:yes"),
                ("❌ Нет", f"case:{name}:no"),
            ],
            [("❔ Неизвестно", f"case:{name}:unknown")],
        ]
    )
    for name in ("formal", "harm", "hospital", "authority")
}
DOCUMENTS_KEYBOARD = _keyboard(
    [
        [("✅ Основные документы есть", "case:documents:COMPLETE")],
        [("🟡 Есть не всё", "case:documents:PARTIAL")],
        [("❌ Документов пока нет", "case:documents:NONE")],
    ]
)


def _signal_answer(callback: str, name: str) -> str | None:
    prefix = f"case:{name}:"
    if not callback.startswith(prefix):
        return None
    value = callback.removeprefix(prefix).upper()
    return value if value in {"YES", "NO", "UNKNOWN"} else None
def confirm_keyboard(workflow_id: UUID) -> InlineKeyboardMarkup:
    callback = f"case:confirm:{workflow_id}"
    if len(callback.encode()) > 64:
        raise ValueError("Telegram callback is too long")
    return _keyboard(
        [[("✅ Сформировать отчёт", callback), ("❌ Отменить", "case:cancel")]]
    )


def _user_data(context: ContextTypes.DEFAULT_TYPE) -> dict[Any, Any]:
    data = context.user_data
    if data is None:
        raise LegalCoreApiError(503, "CONVERSATION_STORAGE_UNAVAILABLE", "Storage unavailable")
    return data


def _wizard_data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    user_data = _user_data(context)
    data = user_data.get(WIZARD_DATA_KEY)
    if not isinstance(data, dict):
        data = {}
        user_data[WIZARD_DATA_KEY] = data
    return data


def _clear_wizard(context: ContextTypes.DEFAULT_TYPE) -> None:
    _user_data(context).pop(WIZARD_DATA_KEY, None)


def _legal_core(context: ContextTypes.DEFAULT_TYPE) -> LegalCoreClient:
    client = context.bot_data.get(LEGAL_CORE_CLIENT_KEY)
    if client is None:
        raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "Legal Core unavailable")
    return cast(LegalCoreClient, client)


def _actor_id(update: Update) -> int | None:
    return None if update.effective_user is None else update.effective_user.id


async def _answer_callback(update: Update) -> str | None:
    query = update.callback_query
    if query is None or not isinstance(query.data, str):
        return None
    await query.answer()
    return query.data


async def case_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_callback(update)
    _clear_wizard(context)
    actor_id = _actor_id(update)
    if actor_id is None:
        await _reply(update, "Не удалось определить администратора. Откройте /menu.")
        return ConversationHandler.END

    try:
        actor = await _legal_core(context).get_actor(actor_id)
    except LegalCoreApiError as exc:
        _clear_wizard(context)
        if exc.code == "SUBSCRIPTION_INACTIVE":
            await _reply(
                update,
                "🔒 Доступ по подписке сейчас не активен. "
                "Обратитесь в поддержку сервиса для подключения или продления.",
            )
        elif exc.status_code == 403 or exc.code == "ACTOR_NOT_AUTHORIZED":
            await _reply(
                update,
                "🔒 Ваш аккаунт ещё не подключён как администратор. "
                "Отправьте владельцу бота Telegram ID из команды /whoami.",
            )
        else:
            await _reply(update, "⚠️ Legal Core временно недоступен. Попробуйте открыть кейс позже.")
        return ConversationHandler.END

    if actor.get("role") != "CLINIC_ADMIN":
        await _reply(update, "⚠️ Legal Core вернул некорректный ответ. Попробуйте позже.")
        return ConversationHandler.END
    _user_data(context)[WIZARD_DATA_KEY] = {"workflow_id": str(uuid4())}
    await _reply(
        update,
        "📝 Новая карточка открыта. Кейс будет создан после вашей проверки.\n\n"
        "Указывайте только обезличенные сведения — без ФИО, телефона, адреса, "
        "номера карты и файлов пациента.\n\nЧто произошло?",
    )
    message = update.effective_message
    if message is not None:
        await message.reply_text("Выберите основной тип ситуации:", reply_markup=INCIDENT_KEYBOARD)
    return WizardState.INCIDENT


async def choose_incident(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    if data is None:
        return WizardState.INCIDENT
    value = data.removeprefix("case:incident:")
    allowed = {
        "QUALITY_COMPLAINT",
        "PAYMENT_DISPUTE",
        "INFORMED_CONSENT",
        "PERSONAL_DATA",
        "OTHER",
    }
    if value not in allowed:
        return WizardState.INCIDENT
    _wizard_data(context)["incident_type"] = value
    await _reply(update, "1/10. Какая стоматологическая услуга была оказана? Кратко, без ФИО.")
    return WizardState.SERVICE_TYPE


def _message_text(update: Update) -> str:
    message = update.effective_message
    value = None if message is None else message.text
    return "" if not isinstance(value, str) else value.strip()


async def record_service_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = _message_text(update)
    if not 2 <= len(value) <= 120:
        await _reply(update, "Введите от 2 до 120 символов, например: «установка коронки».")
        return WizardState.SERVICE_TYPE
    _wizard_data(context)["service_type"] = value
    await _reply(update, "2/10. Дата оказания услуги в формате ГГГГ-ММ-ДД:")
    return WizardState.SERVICE_DATE


async def _record_date(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    field: str,
    current: WizardState,
    following: WizardState,
    prompt: str,
    allow_future: bool = False,
) -> int:
    value = parse_date_answer(_message_text(update), allow_future=allow_future)
    if value is None:
        qualifier = "" if allow_future else " не позднее сегодняшней"
        await _reply(
            update,
            f"Нужна существующая дата{qualifier}: ГГГГ-ММ-ДД, либо «неизвестно».",
        )
        return current
    _wizard_data(context)[field] = value
    await _reply(update, prompt)
    return following


async def record_service_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_date(
        update,
        context,
        field="service_date",
        current=WizardState.SERVICE_DATE,
        following=WizardState.INCIDENT_DATE,
        prompt="3/10. Когда произошла проблемная ситуация? ГГГГ-ММ-ДД:",
    )


async def record_incident_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_date(
        update,
        context,
        field="incident_date",
        current=WizardState.INCIDENT_DATE,
        following=WizardState.CLAIM_DATE,
        prompt="4/10. Когда пациент впервые обратился с претензией? ГГГГ-ММ-ДД:",
    )


async def record_claim_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_date(
        update,
        context,
        field="claim_date",
        current=WizardState.CLAIM_DATE,
        following=WizardState.PROBLEM_SUMMARY,
        prompt=(
            "5/10. Опишите, что произошло, нейтрально и по фактам (10–1500 символов). "
            "Не указывайте ФИО и медицинские идентификаторы."
        ),
    )


async def record_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = _message_text(update)
    if not 10 <= len(value) <= 1500:
        await _reply(update, "Описание должно содержать от 10 до 1500 символов.")
        return WizardState.PROBLEM_SUMMARY
    _wizard_data(context)["problem_summary"] = value
    message = update.effective_message
    if message is not None:
        await message.reply_text("6/10. Чего требует пациент?", reply_markup=DEMAND_KEYBOARD)
    return WizardState.PATIENT_DEMAND


async def choose_demand(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    if data is None:
        return WizardState.PATIENT_DEMAND
    value = data.removeprefix("case:demand:")
    allowed = {
        "NO_SPECIFIC_DEMAND",
        "REWORK_DEMAND",
        "REFUND_DEMAND",
        "COMPENSATION_DEMAND",
    }
    if value not in allowed:
        return WizardState.PATIENT_DEMAND
    _wizard_data(context)["patient_demand"] = value
    if value in {"REFUND_DEMAND", "COMPENSATION_DEMAND"}:
        await _reply(update, "Укажите требуемую сумму в рублях, только число:")
        return WizardState.DEMAND_AMOUNT
    await _ask_formal_claim(update)
    return WizardState.FORMAL_CLAIM


async def record_demand_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount_kopecks = parse_ruble_amount_to_kopecks(_message_text(update))
    if amount_kopecks is None:
        await _reply(
            update,
            "Введите сумму от 0,01 до 1 000 000 000 рублей, не более двух знаков после запятой.",
        )
        return WizardState.DEMAND_AMOUNT
    _wizard_data(context)["demand_amount_kopecks"] = amount_kopecks
    await _ask_formal_claim(update)
    return WizardState.FORMAL_CLAIM


async def _ask_formal_claim(update: Update) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "7/10. Поступила письменная претензия?", reply_markup=YES_NO_KEYBOARDS["formal"]
        )


async def choose_formal_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    formal = None if data is None else _signal_answer(data, "formal")
    if formal is None:
        return WizardState.FORMAL_CLAIM
    _wizard_data(context)["formal_claim"] = formal
    if formal == "YES":
        await _reply(update, "Дата получения письменной претензии, ГГГГ-ММ-ДД:")
        return WizardState.CLAIM_RECEIVED_AT
    await _ask_harm(update)
    return WizardState.HARM


async def record_claim_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_date(
        update,
        context,
        field="claim_received_at",
        current=WizardState.CLAIM_RECEIVED_AT,
        following=WizardState.CLAIM_DEADLINE,
        prompt="Срок ответа из документа, ГГГГ-ММ-ДД:",
    )


async def record_claim_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    result = await _record_date(
        update,
        context,
        field="response_deadline",
        current=WizardState.CLAIM_DEADLINE,
        following=WizardState.HARM,
        prompt="8/10. Заявляет ли пациент о вреде здоровью?",
        allow_future=True,
    )
    if result == WizardState.HARM and update.effective_message is not None:
        await update.effective_message.reply_text(
            "Выберите вариант:", reply_markup=YES_NO_KEYBOARDS["harm"]
        )
    return result


async def _ask_harm(update: Update) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "8/10. Заявляет ли пациент о вреде здоровью?",
            reply_markup=YES_NO_KEYBOARDS["harm"],
        )


async def choose_harm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    harm = None if data is None else _signal_answer(data, "harm")
    if harm is None:
        return WizardState.HARM
    _wizard_data(context)["harm_claimed"] = harm
    if harm in {"YES", "UNKNOWN"}:
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                "Была госпитализация?", reply_markup=YES_NO_KEYBOARDS["hospital"]
            )
        return WizardState.HOSPITALIZATION
    await _ask_lawyer(update)
    return WizardState.LAWYER


async def choose_hospitalization(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    hospitalization = None if data is None else _signal_answer(data, "hospital")
    if hospitalization is None:
        return WizardState.HOSPITALIZATION
    _wizard_data(context)["hospitalization"] = hospitalization
    await _ask_lawyer(update)
    return WizardState.LAWYER


async def _ask_lawyer(update: Update) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "9/12. Связывался ли с клиникой представитель или юрист пациента?",
            reply_markup=YES_NO_KEYBOARDS["lawyer"],
        )


async def choose_lawyer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    lawyer = None if data is None else _signal_answer(data, "lawyer")
    if lawyer is None:
        return WizardState.LAWYER
    _wizard_data(context)["lawyer_contact"] = lawyer
    if lawyer == "YES":
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                "Подтверждены полномочия представителя?",
                reply_markup=YES_NO_KEYBOARDS["representative"],
            )
        return WizardState.REPRESENTATIVE_AUTHORITY
    await _ask_authority(update)
    return WizardState.AUTHORITY


async def choose_representative_authority(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    data = await _answer_callback(update)
    authority = None if data is None else _signal_answer(data, "representative")
    if authority is None:
        return WizardState.REPRESENTATIVE_AUTHORITY
    _wizard_data(context)["representative_authority"] = authority
    await _reply(update, "Срок ответа представителю, ГГГГ-ММ-ДД или «неизвестно»:")
    return WizardState.LAWYER_DEADLINE


async def record_lawyer_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = parse_date_answer(_message_text(update), allow_future=True)
    if value is None:
        await _reply(update, "Нужна существующая дата: ГГГГ-ММ-ДД, либо «неизвестно».")
        return WizardState.LAWYER_DEADLINE
    data = _wizard_data(context)
    selected, changed = _earliest_known_deadline(data.get("response_deadline"), value)
    data["response_deadline"] = selected
    if changed:
        await _reply(
            update,
            "У претензии и обращения представителя разные сроки. "
            f"В карточке учтён ближайший: {selected['date']}.",
        )
    await _reply(update, "10/12. Есть обращение в суд или контролирующий орган?")
    message = update.effective_message
    if message is not None:
        await message.reply_text("Выберите вариант:", reply_markup=YES_NO_KEYBOARDS["authority"])
    return WizardState.AUTHORITY


async def _ask_authority(update: Update) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "10/12. Есть обращение в суд или контролирующий орган?",
            reply_markup=YES_NO_KEYBOARDS["authority"],
        )


async def choose_authority(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    authority = None if data is None else _signal_answer(data, "authority")
    if authority is None:
        return WizardState.AUTHORITY
    _wizard_data(context)["regulator_or_court"] = authority
    if authority == "YES":
        await _reply(update, "Какой орган или суд направил документ? Без персональных данных.")
        return WizardState.AUTHORITY_KIND
    await _ask_regulator_threat(update)
    return WizardState.REGULATOR_THREAT


async def record_authority_kind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = _message_text(update)
    if not 2 <= len(value) <= 120:
        await _reply(update, "Введите название органа: от 2 до 120 символов.")
        return WizardState.AUTHORITY_KIND
    _wizard_data(context)["authority_kind"] = value
    await _reply(update, "Дата документа органа, ГГГГ-ММ-ДД:")
    return WizardState.AUTHORITY_DATE


async def record_authority_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _record_date(
        update,
        context,
        field="authority_document_date",
        current=WizardState.AUTHORITY_DATE,
        following=WizardState.AUTHORITY_DEADLINE,
        prompt="Срок ответа из документа, ГГГГ-ММ-ДД:",
    )


async def record_authority_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = parse_date_answer(_message_text(update), allow_future=True)
    if value is None:
        await _reply(update, "Нужна существующая дата: ГГГГ-ММ-ДД, либо «неизвестно».")
        return WizardState.AUTHORITY_DEADLINE
    data = _wizard_data(context)
    existing = data.get("response_deadline")
    value, changed = _earliest_known_deadline(existing, value)
    if changed:
        await _reply(
            update,
            "У претензии и документа органа разные сроки. "
            f"В карточке учтён ближайший: {value['date']}.",
        )
    data["response_deadline"] = value
    await _ask_regulator_threat(update)
    return WizardState.REGULATOR_THREAT


async def _ask_regulator_threat(update: Update) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "11/12. Есть угроза обращения в суд или контролирующий орган?",
            reply_markup=YES_NO_KEYBOARDS["regulator_threat"],
        )


async def choose_regulator_threat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    threat = None if data is None else _signal_answer(data, "regulator_threat")
    if threat is None:
        return WizardState.REGULATOR_THREAT
    _wizard_data(context)["regulator_threat"] = threat
    await _ask_documents(update)
    return WizardState.DOCUMENTS


async def _ask_documents(update: Update) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "12/12. Есть договор, медкарта и информированное согласие?\n"
            "Сами файлы сюда не загружайте.",
            reply_markup=DOCUMENTS_KEYBOARD,
        )


def _draft_date_value(data: dict[str, Any], field: str) -> str | DateFactValue:
    value = data[field]
    if isinstance(value, str):
        return value
    if (
        isinstance(value, dict)
        and set(value) == {"date", "precision"}
        and (value["date"] is None or isinstance(value["date"], str))
        and isinstance(value["precision"], str)
    ):
        return {"date": value["date"], "precision": value["precision"]}
    raise ValueError(f"invalid wizard date: {field}")


def _known_date_value(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        candidate = value.get("date")
        if isinstance(candidate, str):
            return candidate
    return None


def _earliest_known_deadline(
    existing: object, candidate: DateFactValue
) -> tuple[DateFactValue, bool]:
    existing_date = _known_date_value(existing)
    candidate_date = _known_date_value(candidate)
    if existing_date is None:
        return candidate, False
    if candidate_date is None:
        return {"date": existing_date, "precision": "EXACT"}, False
    selected_date = min(existing_date, candidate_date)
    return {"date": selected_date, "precision": "EXACT"}, existing_date != candidate_date


def _draft_signal_value(data: dict[str, Any], field: str) -> SignalAnswer:
    value = data[field]
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value in {"YES", "NO", "UNKNOWN"}:
        return cast(SignalAnswer, value)
    raise ValueError(f"invalid wizard signal: {field}")


def _optional_draft_date_value(data: dict[str, Any], field: str) -> str | DateFactValue | None:
    return None if field not in data else _draft_date_value(data, field)


def _optional_draft_signal_value(data: dict[str, Any], field: str) -> SignalAnswer | None:
    return None if field not in data else _draft_signal_value(data, field)


def _draft_from_data(data: dict[str, Any]) -> CaseDraft:
    return CaseDraft(
        incident_type=str(data["incident_type"]),
        service_type=str(data["service_type"]),
        service_date=_draft_date_value(data, "service_date"),
        incident_date=_draft_date_value(data, "incident_date"),
        claim_date=_draft_date_value(data, "claim_date"),
        problem_summary=str(data["problem_summary"]),
        patient_demand=str(data["patient_demand"]),
        formal_claim=_draft_signal_value(data, "formal_claim"),
        harm_claimed=_draft_signal_value(data, "harm_claimed"),
        regulator_or_court=_draft_signal_value(data, "regulator_or_court"),
        documents_status=str(data["documents_status"]),
        demand_amount_kopecks=data.get("demand_amount_kopecks"),
        claim_received_at=_optional_draft_date_value(data, "claim_received_at"),
        response_deadline=_optional_draft_date_value(data, "response_deadline"),
        hospitalization=_optional_draft_signal_value(data, "hospitalization"),
        authority_kind=data.get("authority_kind"),
        authority_document_date=_optional_draft_date_value(data, "authority_document_date"),
        lawyer_contact=_optional_draft_signal_value(data, "lawyer_contact") or False,
        representative_authority=_optional_draft_signal_value(data, "representative_authority"),
        regulator_threat=_optional_draft_signal_value(data, "regulator_threat") or False,
    )


async def choose_documents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    callback = await _answer_callback(update)
    if callback is None:
        return WizardState.DOCUMENTS
    status_value = callback.removeprefix("case:documents:")
    if status_value not in {"COMPLETE", "PARTIAL", "NONE"}:
        return WizardState.DOCUMENTS
    data = _wizard_data(context)
    data["documents_status"] = status_value
    try:
        facts_from_draft(_draft_from_data(data))
    except (KeyError, ValueError):
        await _reply(update, "Карточка неполная. Отмените её и начните заново через /menu.")
        return WizardState.CONFIRM
    await _reply(
        update,
        "✅ Карточка заполнена.\n\n"
        f"Услуга: {data['service_type']}\n"
        f"Дата ситуации: {data['incident_date']}\n"
        "Проверьте, что в описании нет ФИО и иных идентификаторов.",
    )
    message = update.effective_message
    if message is not None:
        workflow_id = UUID(str(data["workflow_id"]))
        await message.reply_text(
            "Сформировать единый PDF-отчёт?",
            reply_markup=confirm_keyboard(workflow_id),
        )
    return WizardState.CONFIRM


async def _send_workflow_report(
    update: Update,
    client: LegalCoreClient,
    workflow: dict[str, Any],
    actor_id: int,
) -> None:
    case = workflow.get("case")
    report = workflow.get("report")
    if workflow.get("state") != "SUCCEEDED" or not isinstance(case, dict):
        raise ValueError("workflow is not complete")
    if not isinstance(report, dict):
        raise ValueError("workflow report is missing")
    public_number = case.get("publicNumber")
    report_id_value = report.get("id")
    report_json = report.get("reportJson")
    if not isinstance(public_number, str) or not isinstance(report_json, dict):
        raise ValueError("workflow response is invalid")
    report_id = UUID(str(report_id_value))
    telegram_summary = telegram_summary_from_report(report_json)
    pdf = await client.download_pdf(report_id, actor_id)
    safe_number = re.sub(r"[^A-Za-z0-9_-]", "-", public_number)[:64]
    message = update.effective_message
    if message is not None:
        await message.reply_text(telegram_summary)
        await message.reply_document(
            document=InputFile(BytesIO(pdf), filename=f"{safe_number}.pdf"),
            caption=(
                f"✅ Отчёт по кейсу {public_number} готов.\n"
                "Это структурированная карточка, не юридическое заключение. "
                "Автоматическая отправка пациенту отключена."
            ),
        )


async def resume_workflow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback = await _answer_callback(update)
    actor_id = _actor_id(update)
    if callback is None or actor_id is None:
        return
    try:
        workflow_id = UUID(callback.removeprefix("case:confirm:"))
        client = _legal_core(context)
        workflow = await client.get_workflow(workflow_id, actor_id)
        await _send_workflow_report(update, client, workflow, actor_id)
    except (ValueError, LegalCoreApiError) as exc:
        logger.warning("workflow recovery failed: %s", type(exc).__name__)
        if isinstance(exc, LegalCoreApiError) and exc.status_code == 404:
            await _reply(
                update,
                "Эта карточка не была подтверждена. Откройте /menu и заполните её заново.",
            )
        elif isinstance(exc, LegalCoreApiError) and exc.status_code == 403:
            await _reply(update, "🔒 Доступ администратора отозван.")
        else:
            await _reply(update, "⚠️ Не удалось получить отчёт. Попробуйте ещё раз.")


async def confirm_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    callback = await _answer_callback(update)
    if callback == "case:cancel":
        return await cancel_case(update, context)
    if callback is None or not callback.startswith("case:confirm:"):
        return WizardState.CONFIRM
    actor_id = _actor_id(update)
    data = _wizard_data(context)
    if actor_id is None:
        _clear_wizard(context)
        return ConversationHandler.END
    try:
        workflow_id = UUID(callback.removeprefix("case:confirm:"))
        if workflow_id != UUID(str(data["workflow_id"])):
            raise ValueError("workflow callback does not match active conversation")
        facts = facts_from_draft(_draft_from_data(data))
        client = _legal_core(context)
        workflow = await client.submit_workflow(workflow_id, facts, actor_id)
        await _send_workflow_report(update, client, workflow, actor_id)
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        if isinstance(exc, LegalCoreApiError) and exc.status_code == 403:
            _clear_wizard(context)
            await _reply(update, "🔒 Доступ администратора отозван. Обратитесь к владельцу бота.")
            return ConversationHandler.END
        logger.warning("case report generation failed: %s", type(exc).__name__)
        await _reply(
            update,
            "⚠️ Отчёт пока не сформирован. Нажмите «Сформировать отчёт» ещё раз или /cancel.",
        )
        return WizardState.CONFIRM

    _clear_wizard(context)
    return ConversationHandler.END


async def cancel_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is not None and query.data == "case:cancel":
        # ``confirm_case`` may already have answered this callback.
        pass
    _clear_wizard(context)
    await _reply(
        update,
        "Сбор данных остановлен. Новая отправка не выполняется; "
        "повтор уже подтверждённой кнопки вернёт тот же отчёт.",
    )
    return ConversationHandler.END


async def timeout_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_wizard(context)
    await _reply(update, "Время заполнения истекло. Откройте /menu, чтобы начать новый кейс.")
    return ConversationHandler.END


async def exit_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_wizard(context)
    await start(update, context)
    return ConversationHandler.END


async def exit_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_wizard(context)
    await menu_callback(update, context)
    return ConversationHandler.END


async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    del context
    await _reply(update, TEXT_INPUT_DISABLED_MESSAGE)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    del context
    query = update.callback_query
    if query is None:
        return

    callback_data = query.data
    allowed_callbacks = MAIN_MENU_CALLBACKS | {"menu"}
    if not isinstance(callback_data, str) or callback_data not in allowed_callbacks:
        await query.answer("Меню обновилось. Откройте /menu.", show_alert=True)
        return

    # Callback queries must be answered, even when no toast is needed.
    # Source: https://docs.python-telegram-bot.org/en/v22.7/examples.inlinekeyboard.html
    await query.answer()
    keyboard = main_menu_keyboard() if callback_data == "menu" else back_keyboard()
    await query.edit_message_caption(caption=SCREENS[callback_data], reply_markup=keyboard)


async def on_startup(application: TelegramApplication) -> None:
    # PTB 22.8 runs post_init after Bot.initialize/getMe and before polling. Cosmetic,
    # rate-limited profile mutations live in telegram_gateway.profile and are never
    # allowed to make polling unavailable.
    # Source: https://docs.python-telegram-bot.org/en/v22.8/telegram.ext.application.html
    bot_data = getattr(application, "bot_data", None)
    if isinstance(bot_data, dict):
        bot_data[LEGAL_CORE_CLIENT_KEY] = LegalCoreClient(
            httpx2.AsyncClient(
                base_url=load_legal_core_url(),
                timeout=LEGAL_CORE_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            )
        )
    READY_FILE.touch(mode=0o600)
    logger.info("telegram gateway initialized")


async def on_shutdown(application: TelegramApplication) -> None:
    bot_data = getattr(application, "bot_data", {})
    client = bot_data.pop(LEGAL_CORE_CLIENT_KEY, None)
    if isinstance(client, LegalCoreClient):
        await client.aclose()
    READY_FILE.unlink(missing_ok=True)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    del update
    logger.error("telegram update failed: %s", type(context.error).__name__)


def build_application(token: str) -> TelegramApplication:
    # Application.builder + async handlers follows the official v22.8 pattern.
    # Source: https://docs.python-telegram-bot.org/en/v22.8/examples.echobot.html
    application = (
        Application.builder()
        .token(token)
        .concurrent_updates(False)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(case_start, pattern=r"^case:start$")],
            states={
                WizardState.INCIDENT: [
                    CallbackQueryHandler(choose_incident, pattern=r"^case:incident:")
                ],
                WizardState.SERVICE_TYPE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_service_type)
                ],
                WizardState.SERVICE_DATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_service_date)
                ],
                WizardState.INCIDENT_DATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_incident_date)
                ],
                WizardState.CLAIM_DATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_claim_date)
                ],
                WizardState.PROBLEM_SUMMARY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_summary)
                ],
                WizardState.PATIENT_DEMAND: [
                    CallbackQueryHandler(choose_demand, pattern=r"^case:demand:")
                ],
                WizardState.DEMAND_AMOUNT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_demand_amount)
                ],
                WizardState.FORMAL_CLAIM: [
                    CallbackQueryHandler(choose_formal_claim, pattern=r"^case:formal:")
                ],
                WizardState.CLAIM_RECEIVED_AT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_claim_received)
                ],
                WizardState.CLAIM_DEADLINE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_claim_deadline)
                ],
                WizardState.HARM: [
                    CallbackQueryHandler(choose_harm, pattern=r"^case:harm:")
                ],
                WizardState.HOSPITALIZATION: [
                    CallbackQueryHandler(choose_hospitalization, pattern=r"^case:hospital:")
                ],
                WizardState.LAWYER: [
                    CallbackQueryHandler(choose_lawyer, pattern=r"^case:lawyer:")
                ],
                WizardState.REPRESENTATIVE_AUTHORITY: [
                    CallbackQueryHandler(
                        choose_representative_authority, pattern=r"^case:representative:"
                    )
                ],
                WizardState.LAWYER_DEADLINE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_lawyer_deadline)
                ],
                WizardState.AUTHORITY: [
                    CallbackQueryHandler(choose_authority, pattern=r"^case:authority:")
                ],
                WizardState.AUTHORITY_KIND: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_authority_kind)
                ],
                WizardState.AUTHORITY_DATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_authority_date)
                ],
                WizardState.AUTHORITY_DEADLINE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, record_authority_deadline)
                ],
                WizardState.REGULATOR_THREAT: [
                    CallbackQueryHandler(
                        choose_regulator_threat, pattern=r"^case:regulator_threat:"
                    )
                ],
                WizardState.DOCUMENTS: [
                    CallbackQueryHandler(choose_documents, pattern=r"^case:documents:")
                ],
                WizardState.CONFIRM: [
                    CallbackQueryHandler(
                        confirm_case,
                        pattern=(
                            r"^(case:cancel|case:confirm:"
                            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                            r"[0-9a-f]{4}-[0-9a-f]{12})$"
                        ),
                    )
                ],
                ConversationHandler.TIMEOUT: [
                    MessageHandler(filters.ALL, timeout_case),
                    CallbackQueryHandler(timeout_case),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", cancel_case),
                CommandHandler(["start", "menu"], exit_to_menu),
                CallbackQueryHandler(
                    exit_to_menu_callback,
                    pattern=r"^(menu|features|workflow|privacy|about)$",
                ),
            ],
            allow_reentry=True,
            conversation_timeout=15 * 60,
            name="administrator-case-intake-v1",
        )
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("grant_access", grant_access))
    application.add_handler(
        CallbackQueryHandler(
            resume_workflow,
            pattern=(
                r"^case:confirm:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}$"
            ),
        )
    )
    application.add_handler(CallbackQueryHandler(menu_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    application.add_error_handler(on_error)
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    application = build_application(load_token())
    # Limit the Bot API subscription to messages; unused update types are not requested.
    # Source: https://docs.python-telegram-bot.org/en/v22.8/telegram.ext.application.html
    application.run_polling(
        allowed_updates=ALLOWED_UPDATES,
        bootstrap_retries=3,
        drop_pending_updates=False,
    )
