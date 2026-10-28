# ruff: noqa: RUF001
"""Safety-scoped Telegram polling gateway."""

import asyncio
import logging
import os
import re
from collections.abc import Callable, Coroutine, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import IntEnum
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, TypeAlias, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx2
from legal_core.case_material_contracts import CaseMaterialListResponse
from legal_core.contracts import TelegramPreparationReceipt, TelegramWorkflowResponse
from legal_core.pseudonymization import pseudonymize_text
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message, Update
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from telegram_gateway.case_wizard import (
    V2_INTAKE_SCHEMA_VERSION,
    CaseDraft,
    DateFactValue,
    LegalCoreApiError,
    LegalCoreClient,
    SignalAnswer,
    confirmed_clarification_answers,
    facts_from_draft,
    facts_from_v2_data,
    parse_date_answer,
    parse_ruble_amount_to_kopecks,
    telegram_summary_from_report,
)
from telegram_gateway.clinic_team_ui import clinic_team_text
from telegram_gateway.factual_safety_intake import (
    answer_screening,
    explicitly_skip_other_questions,
    known_urgent_report,
    previous_screening_question,
    screening_complete,
    screening_question,
)
from telegram_gateway.guided_case_intake_v2 import (
    V2_EVENT,
    V2_INCOMING,
    V2_SITUATION,
    apply_clarification_answer,
    clarification_choices,
    clarification_question,
    clarification_requests,
    invalidate_service_clarification_answers,
    next_clarification_request,
    previous_clarification_question,
)
from telegram_gateway.guided_case_intake_v2 import (
    active_states as v2_active_states,
)
from telegram_gateway.guided_case_intake_v2 import (
    choice_items as v2_choice_items,
)
from telegram_gateway.guided_case_intake_v2 import (
    next_missing_state as v2_next_missing_state,
)
from telegram_gateway.guided_case_intake_v2 import (
    parse_answer as parse_v2_answer,
)
from telegram_gateway.guided_case_intake_v2 import review_pages as v2_review_pages
from telegram_gateway.quick_intake import contains_probable_person_name
from telegram_gateway.ui import (
    HELP_MESSAGE,
    MAIN_MENU_CALLBACKS,
    SCREENS,
    START_MESSAGE,
    TEXT_INPUT_DISABLED_MESSAGE,
    WELCOME_IMAGE,
    admin_panel_keyboard,
    back_keyboard,
    clinic_team_keyboard,
    main_menu_keyboard,
)
from telegram_gateway.update_processor import ActorSerialUpdateProcessor
from telegram_gateway.workflow_preparation import (
    decode_workflow_confirmation,
    preparation_workflow_message,
)

logger = logging.getLogger(__name__)

TelegramApplication: TypeAlias = Application[Any, Any, Any, Any, Any, Any]
TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
READY_FILE = Path("/tmp/telegram-gateway-ready")
POLLING_HEARTBEAT_FILE = Path("/tmp/telegram-gateway-last-update")
ALLOWED_UPDATES = ["message", "callback_query"]
LEGAL_CORE_CLIENT_KEY = "legal_core_client"
WIZARD_DATA_KEY = "case_wizard"
DRAFT_ID_KEY = "draft_id"
DRAFT_REVISION_KEY = "draft_revision"
DRAFT_STATE_KEY = "draft_state"
ADMIN_GRANT_ACCESS_KEY = "admin_grant_access"
ADMIN_GRANT_PILOT_KEY = "admin_grant_pilot"
TEAM_MEMBER_ROLE_KEY = "team_member_role"
LEGAL_CORE_TIMEOUT_SECONDS = 15.0
POLLING_WATCHDOG_INTERVAL_SECONDS = 30
POLLING_STALL_SECONDS = 120
TELEGRAM_API_FAILURES_KEY = "telegram_api_consecutive_failures"
TELEGRAM_API_FAILURE_LIMIT = 3
TELEGRAM_BOT_API_POOL_SIZE = 8
TELEGRAM_POLLING_POOL_SIZE = 2
TELEGRAM_POOL_TIMEOUT_SECONDS = 10.0
TELEGRAM_MAX_CONCURRENT_ACTORS = 8
TELEGRAM_QUEUED_UPDATE_LIMIT = 64
CASE_INTAKE_ROLES = frozenset({"CLINIC_OWNER", "CLINIC_ADMIN"})
CALLBACK_ERROR_MESSAGE = "⚠️ Не удалось выполнить действие. Откройте /menu и попробуйте ещё раз."
_DRAFT_DELETE_CALLBACK_RE = re.compile(
    r"^case:draft:delete(?::confirm)?:"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
_DRAFT_DELETE_CONFIRM_PREFIX = "case:draft:delete:confirm:"


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
    INCOMING = 24
    SITUATION = 25
    SERVICES = 26
    EVENT = 27
    EVENT_DATE = 28
    CHRONOLOGY = 29
    CLINIC_ACTIONS = 30
    HEALTH = 31
    MATERIALS = 32
    SUMMARY = 33
    V2_CONFIRM = 34
    SAFETY = 35
    ANALYSIS_CLARIFICATION = 36


def load_token(environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    token = source.get("TELEGRAM_BOT_TOKEN", "").strip()
    if len(token) < 20 or TOKEN_PATTERN.fullmatch(token) is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing or malformed")
    return token


def load_telegram_proxy_url(environment: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if environment is None else environment
    value = source.get("TELEGRAM_PROXY_URL", "").strip()
    if not value:
        return None

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise RuntimeError("TELEGRAM_PROXY_URL is malformed") from error

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("TELEGRAM_PROXY_URL is malformed")
    return value.rstrip("/")


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


def load_legal_editor_gateway_key(
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Load the optional, dedicated gateway-to-Core editor credential.

    The ordinary bot remains usable when the workspace has not yet been configured. A malformed
    optional value hides the high-privilege control instead of preventing polling from starting.
    """

    source = os.environ if environment is None else environment
    value = source.get("LEGAL_EDITOR_GATEWAY_KEY", "").strip()
    if not value:
        return None
    if len(value) < 32:
        logger.error("LEGAL_EDITOR_GATEWAY_KEY is too short; legal editor workspace disabled")
        return None
    return value


def _polling_is_stalled(
    *,
    pending_update_count: int,
    heartbeat_at: datetime,
    now: datetime,
) -> bool:
    """Detect a stalled poller without restarting an idle bot."""

    return (
        pending_update_count > 0
        and (now - heartbeat_at).total_seconds() > POLLING_STALL_SECONDS
    )


def _last_update_heartbeat() -> datetime | None:
    try:
        modified_at = POLLING_HEARTBEAT_FILE.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(modified_at, UTC)


def _telegram_api_failure_count(application: object) -> int:
    """Return only a bounded, process-local liveness counter, never user data."""

    bot_data = getattr(application, "bot_data", None)
    if not isinstance(bot_data, dict):
        return 0
    value = bot_data.get(TELEGRAM_API_FAILURES_KEY, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _record_telegram_api_success(application: object) -> None:
    bot_data = getattr(application, "bot_data", None)
    if isinstance(bot_data, dict):
        bot_data.pop(TELEGRAM_API_FAILURES_KEY, None)


def _record_telegram_api_failure(application: object) -> int:
    bot_data = getattr(application, "bot_data", None)
    failures = _telegram_api_failure_count(application) + 1
    if isinstance(bot_data, dict):
        bot_data[TELEGRAM_API_FAILURES_KEY] = failures
    return failures


def _mark_telegram_transport_unready(*, failures: int) -> None:
    """Expose a repeated transport outage without interrupting PTB's retry loop.

    The gateway must not keep reporting ready when its only Telegram transport is unavailable.
    `Updater.start_polling` already owns an indefinite retry loop for failed polling requests;
    stopping the application here discarded that recovery path and added a container restart.
    A direct fallback would expose traffic outside the configured tunnel and is deliberately absent.
    """

    READY_FILE.unlink(missing_ok=True)
    logger.error(
        "Telegram Bot API unavailable after %s consecutive failures; continuing polling retries",
        failures,
    )


async def _record_update_heartbeat(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Record delivery before feature handlers, including harmless stale callbacks."""

    del update, context
    try:
        POLLING_HEARTBEAT_FILE.touch(mode=0o600)
    except OSError:
        # Processing an update is still safer than failing it because a local
        # liveness marker could not be written.
        logger.warning("could not update Telegram polling heartbeat")


async def _polling_watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart only when Telegram has a backlog the poller has not observed."""

    application = context.application
    try:
        webhook = await application.bot.get_webhook_info()
    except TelegramError:
        failures = _record_telegram_api_failure(application)
        logger.warning("could not inspect Telegram polling backlog")
        if failures == TELEGRAM_API_FAILURE_LIMIT:
            _mark_telegram_transport_unready(failures=failures)
        return
    _record_telegram_api_success(application)
    try:
        READY_FILE.touch(mode=0o600)
    except OSError:
        logger.error("could not restore Telegram polling readiness marker")
    pending_update_count = webhook.pending_update_count
    heartbeat_at = _last_update_heartbeat()
    if heartbeat_at is None:
        logger.error("Telegram polling heartbeat is unavailable; restarting gateway")
        application.stop_running()
        return
    if _polling_is_stalled(
        pending_update_count=pending_update_count,
        heartbeat_at=heartbeat_at,
        now=datetime.now(UTC),
    ):
        logger.error(
            "Telegram polling stalled with %s pending update(s); restarting gateway",
            pending_update_count,
        )
        application.stop_running()


async def _require_private_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Never render clinic data or consume a private intake in a shared chat."""

    del context
    chat = update.effective_chat
    if chat is not None and chat.type == "private":
        return
    notice = "Откройте личный чат с ботом: кейсы и документы доступны только там."
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(notice, show_alert=True)
        elif update.effective_message is not None:
            await update.effective_message.reply_text(notice)
    except TelegramError:
        logger.warning("could not notify user about private chat requirement")
    raise ApplicationHandlerStop


async def _reply(
    update: Update,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    message = update.effective_message
    if message is not None:
        return await message.reply_text(text, reply_markup=reply_markup)
    return None


def _clear_pending_inputs(context: ContextTypes.DEFAULT_TYPE | None) -> None:
    if context is not None and context.user_data is not None:
        context.user_data.pop(ADMIN_GRANT_ACCESS_KEY, None)
        context.user_data.pop(ADMIN_GRANT_PILOT_KEY, None)
        context.user_data.pop(TEAM_MEMBER_ROLE_KEY, None)
        context.user_data.pop("escalation_discussion_id", None)
        context.user_data.pop("quick_intake_pending", None)
        context.user_data.pop("quick_intake_candidate", None)
        context.user_data.pop("clinic_document_upload", None)
        context.user_data.pop("clinic_document_effective_date_pending", None)
        context.user_data.pop("escalation_resolution_pending", None)
        context.user_data.pop("reference_evaluation_pending", None)


def _clear_pending_admin_grant(context: ContextTypes.DEFAULT_TYPE | None) -> None:
    """Compatibility entry point for the lawyer workspace navigation handlers."""
    _clear_pending_inputs(context)


async def _route_input_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Navigation leaves the free-text discussion before any earlier-group consumer runs.

    The wizard remains owned by ConversationHandler; its durable draft is never deleted.
    An old discussion button cannot replace an active wizard (checked by that handler).
    """
    query = update.callback_query
    callback = query.data if query is not None else None
    if isinstance(callback, str) and callback.startswith(("analysis:pdf:", "esc:pdf:")):
        # Read-only background file delivery must not cancel the active text-input mode.
        return
    message = update.effective_message
    command = message.text if query is None and message is not None else None
    navigating = isinstance(callback, str) or (
        isinstance(command, str) and command.startswith("/")
    )
    if not navigating:
        return
    data = _user_data(context)
    if not isinstance(callback, str) or not callback.startswith(
        ("case:escalation:", "case:discussion:", "esc:")
    ):
        data.pop("escalation_discussion_id", None)
        data.pop("escalation_resolution_pending", None)
    # Modes waiting for arbitrary text must not survive navigation to a different flow.
    # Callback handlers establish their new pending state after this boundary runs.
    for key in (ADMIN_GRANT_ACCESS_KEY, ADMIN_GRANT_PILOT_KEY, TEAM_MEMBER_ROLE_KEY):
        data.pop(key, None)
    if not isinstance(callback, str) or not callback.startswith("quick:"):
        data.pop("quick_intake_pending", None)
        data.pop("quick_intake_candidate", None)
    if not isinstance(callback, str) or not callback.startswith("clinicdoc:"):
        data.pop("clinic_document_upload", None)
        data.pop("clinic_document_effective_date_pending", None)
    if not isinstance(callback, str) or not callback.startswith("refeval:"):
        data.pop("reference_evaluation_pending", None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    _clear_pending_inputs(context)
    message = update.effective_message
    if message is not None:
        # Path uploads and inline keyboards follow the official PTB 22.8 APIs.
        # Sources: https://docs.python-telegram-bot.org/en/v22.8/telegram.bot.html
        # https://docs.python-telegram-bot.org/en/v22.8/telegram.inlinekeyboardbutton.html
        await message.reply_photo(
            photo=WELCOME_IMAGE,
            caption=START_MESSAGE,
            reply_markup=await _main_menu_for_actor(update, context),
        )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    await start(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    _clear_pending_inputs(context)
    await _reply(update, HELP_MESSAGE)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    del context
    user = update.effective_user
    if user is None:
        await _reply(update, "Не удалось определить Telegram ID.")
        return
    await _reply(update, f"👤 Ваш Telegram ID: {user.id}")


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    _clear_pending_inputs(context)
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "🛠 ПАНЕЛЬ ВЛАДЕЛЬЦА\n\n"
            "Выберите действие. Права владельца дополнительно проверяет Legal Core.",
            reply_markup=admin_panel_keyboard(),
        )


async def clinic_team(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    _clear_pending_inputs(context)
    actor_id = _actor_id(update)
    if actor_id is None:
        await _reply(update, "Не удалось определить пользователя.")
        return
    try:
        actor = await _legal_core(cast(ContextTypes.DEFAULT_TYPE, context)).get_actor(actor_id)
    except LegalCoreApiError:
        await _reply(update, "⚠️ Не удалось открыть команду клиники. Попробуйте позже.")
        return
    if actor.get("role") != "CLINIC_OWNER":
        await _reply(update, "🔒 Управление командой доступно владельцу клиники.")
        return
    try:
        members = await _legal_core(cast(ContextTypes.DEFAULT_TYPE, context)).list_clinic_members(
            actor_id
        )
        text = clinic_team_text(members.get("items"))
    except (LegalCoreApiError, ValueError):
        await _reply(
            update, "⚠️ Не удалось загрузить команду клиники. Попробуйте позже.",
            reply_markup=back_keyboard(),
        )
        return
    await _reply(
        update,
        text,
        reply_markup=clinic_team_keyboard(),
    )


async def prompt_team_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback = await _answer_callback(update)
    roles = {"team:add:admin": "CLINIC_ADMIN", "team:add:lawyer": "CLINIC_LAWYER"}
    role = roles.get(callback or "")
    if role is None:
        return
    if WIZARD_DATA_KEY in _user_data(context):
        await _reply(update, "Сначала завершите или отмените заполнение текущего кейса.")
        return
    _clear_pending_inputs(context)
    _user_data(context)[TEAM_MEMBER_ROLE_KEY] = role
    title = "администратора" if role == "CLINIC_ADMIN" else "юриста"
    await _reply(
        update, f"Введите Telegram ID {title} одним числом.", reply_markup=back_keyboard()
    )


async def record_team_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    role = _user_data(context).get(TEAM_MEMBER_ROLE_KEY)
    target_id = _target_telegram_id(_message_text(update))
    if role not in {"CLINIC_ADMIN", "CLINIC_LAWYER"} or target_id is None:
        await _reply(update, "Введите корректный Telegram ID одним положительным числом.")
        return
    actor_id = _actor_id(update)
    if actor_id is None:
        await _reply(update, "Не удалось определить владельца клиники.")
        return
    try:
        member = await _legal_core(context).add_clinic_member(actor_id, target_id, role=role)
    except LegalCoreApiError as exc:
        if exc.code == "CLINIC_OWNER_REQUIRED":
            await _reply(update, "🔒 Управление командой доступно владельцу клиники.")
        else:
            await _reply(update, "⚠️ Не удалось добавить пользователя. Попробуйте позже.")
        return
    _user_data(context).pop(TEAM_MEMBER_ROLE_KEY, None)
    label = "администратор" if member.get("role") == "CLINIC_ADMIN" else "юрист"
    await _reply(update, f"✅ Пользователь {target_id} добавлен в команду: {label}.")
    await clinic_team(update, context)


def _target_telegram_id(raw_value: str) -> int | None:
    if not raw_value.isdigit():
        return None
    target_id = int(raw_value)
    return target_id if 0 < target_id <= 9_223_372_036_854_775_807 else None


async def _grant_access_to_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_id: int,
    *,
    plan_code: str = "MVP_MANUAL",
    pilot_days: int | None = None,
) -> None:
    owner_id = _actor_id(update)
    if owner_id is None:
        await _reply(update, "Не удалось определить владельца.")
        return
    try:
        granted = await _legal_core(context).grant_subscription(
            owner_id,
            target_id,
            plan_code=plan_code,
            pilot_days=pilot_days,
        )
    except LegalCoreApiError as exc:
        if exc.code == "OWNER_REQUIRED":
            await _reply(update, "🔒 Эта панель доступна только владельцу сервиса.")
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
    if plan_code == "FREE_PILOT":
        await _reply(
            update,
            f"✅ Бесплатный pilot на {pilot_days} дней выдан для Telegram ID {target_id}. "
            "Попросите пользователя открыть /start и затем /menu.",
        )
    else:
        await _reply(
            update,
            f"✅ Доступ выдан для Telegram ID {target_id}. "
            "Попросите пользователя открыть /start и затем /menu.",
        )


async def grant_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arguments = context.args or []
    if len(arguments) != 1:
        await _reply(update, "Использование: /grant_access <Telegram_ID>")
        return
    target_id = _target_telegram_id(arguments[0])
    if target_id is None:
        await _reply(update, "Telegram ID указан некорректно.")
        return
    await _grant_access_to_target(update, context, target_id)


async def grant_pilot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arguments = context.args or []
    if not 1 <= len(arguments) <= 2:
        await _reply(update, "Использование: /grant_pilot <Telegram_ID> [дни 1-90]")
        return
    target_id = _target_telegram_id(arguments[0])
    if target_id is None:
        await _reply(update, "Telegram ID указан некорректно.")
        return
    pilot_days = 30
    if len(arguments) == 2:
        try:
            pilot_days = int(arguments[1])
        except ValueError:
            pilot_days = 0
    if not 1 <= pilot_days <= 90:
        await _reply(update, "Длительность pilot должна быть от 1 до 90 дней.")
        return
    await _grant_access_to_target(
        update,
        context,
        target_id,
        plan_code="FREE_PILOT",
        pilot_days=pilot_days,
    )


async def prompt_admin_grant_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _answer_callback(update) != "admin:grant":
        return
    if WIZARD_DATA_KEY in _user_data(context):
        await _reply(update, "Сначала завершите или отмените заполнение текущего кейса.")
        return
    _clear_pending_inputs(context)
    _user_data(context)[ADMIN_GRANT_ACCESS_KEY] = True
    await _reply(
        update,
        "Введите Telegram ID пользователя одним числом. "
        "Он получит доступ только после серверной проверки ваших прав владельца.",
    )


async def prompt_admin_grant_pilot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _answer_callback(update) != "admin:pilot":
        return
    if WIZARD_DATA_KEY in _user_data(context):
        await _reply(update, "Сначала завершите или отмените заполнение текущего кейса.")
        return
    _clear_pending_inputs(context)
    _user_data(context)[ADMIN_GRANT_PILOT_KEY] = True
    await _reply(
        update,
        "Введите Telegram ID пользователя одним числом. Будет выдан бесплатный pilot на 30 дней.",
    )


async def record_admin_grant_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target_id = _target_telegram_id(_message_text(update))
    if target_id is None:
        await _reply(update, "Введите корректный Telegram ID одним положительным числом.")
        return
    _user_data(context).pop(ADMIN_GRANT_ACCESS_KEY, None)
    await _grant_access_to_target(update, context, target_id)


async def record_admin_grant_pilot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target_id = _target_telegram_id(_message_text(update))
    if target_id is None:
        await _reply(update, "Введите корректный Telegram ID одним положительным числом.")
        return
    _user_data(context).pop(ADMIN_GRANT_PILOT_KEY, None)
    await _grant_access_to_target(
        update,
        context,
        target_id,
        plan_code="FREE_PILOT",
        pilot_days=30,
    )


def _keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows
    ]
    buttons.extend(list(row) for row in back_keyboard().inline_keyboard)
    return InlineKeyboardMarkup(buttons)


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
    for name in (
        "formal",
        "harm",
        "hospital",
        "lawyer",
        "representative",
        "authority",
        "regulator_threat",
    )
}
DOCUMENTS_KEYBOARD = _keyboard(
    [
        [("✅ Основные документы есть", "case:documents:COMPLETE")],
        [("🟡 Есть не всё", "case:documents:PARTIAL")],
        [("❌ Документов пока нет", "case:documents:NONE")],
    ]
)

_V2_MULTI_FIELDS = frozenset({"situationAreas", "clinicActions", "healthSignals"})
_V2_TEXT_FIELDS = {
    WizardState.SERVICES: "affectedServices",
    WizardState.EVENT: "eventSummary",
    WizardState.EVENT_DATE: "eventDate",
}
_V2_FILE_TYPES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_V2_MAX_UPLOAD_BYTES = 15_000_000


def _v2_callback(value: str) -> str:
    if len(value.encode()) > 64:
        raise ValueError("Telegram v2 callback is too long")
    return value


def _v2_keyboard(
    rows: list[list[tuple[str, str]]], *, with_back: bool = True
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(label, callback_data=_v2_callback(callback))
            for label, callback in row
        ]
        for row in rows
    ]
    if with_back:
        buttons.append([InlineKeyboardButton("← Назад", callback_data="case:v2:back")])
    buttons.extend(list(row) for row in back_keyboard().inline_keyboard)
    return InlineKeyboardMarkup(buttons)


def _v2_single_keyboard(field: str) -> InlineKeyboardMarkup:
    return _v2_keyboard(
        [
            [(label, f"case:v2:single:{field}:{value}")]
            for value, label in v2_choice_items(field)
        ],
        with_back=field != "incomingKind",
    )


def _v2_multi_keyboard(field: str, selected: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            (
                ("✅ " if value in selected else "") + label,
                f"case:v2:multi:{field}:{'-' if value in selected else '+'}{value}",
            )
        ]
        for value, label in v2_choice_items(field)
    ]
    rows.append([("Продолжить →", f"case:v2:multi:{field}:DONE")])
    return _v2_keyboard(rows)


def _v2_state(value: str) -> WizardState:
    try:
        return WizardState[value]
    except KeyError as exc:
        raise ValueError("unknown v2 wizard state") from exc


def _is_v2_draft(data: dict[str, Any]) -> bool:
    return data.get("intakeVersion") == 2


def _saved_draft_selection(
    draft_id: UUID, draft: dict[str, Any], *, is_v2: bool = True,
) -> dict[str, Any]:
    revision, state_name = draft.get("revision"), draft.get("wizardState")
    payload = draft.get("draftData")
    if (
        draft.get("id") != str(draft_id) or type(revision) is not int or revision < 1
        or not isinstance(state_name, str) or not isinstance(payload, dict)
        or (payload.get("intakeVersion") != 2 if is_v2 else "intakeVersion" in payload)
    ):
        raise ValueError("invalid selected draft")
    state = WizardState[state_name]
    if (state.value >= WizardState.INCOMING.value) != is_v2:
        raise ValueError("draft state does not match its version")
    return {
        **payload, "workflow_id": str(draft_id), DRAFT_ID_KEY: str(draft_id),
        DRAFT_REVISION_KEY: revision, DRAFT_STATE_KEY: state.name,
    }


async def _reply_draft_controls(
    update: Update, data: dict[str, Any], text: str, *, reply_markup: InlineKeyboardMarkup,
) -> None:
    message = update.effective_message
    if message is None:
        return
    sent = await message.reply_text(text, reply_markup=reply_markup)
    message_id = getattr(sent, "message_id", None)
    if type(message_id) is int and message_id > 0:
        data["_v2_prompt_message_id"] = message_id


async def _prompt_v2_draft(
    update: Update,
    state: WizardState,
    data: dict[str, Any],
    *, preparation: bool = False, missing_material: bool = False,
) -> None:
    message = update.effective_message
    data.pop("_v2_prompt_message_id", None)

    async def reply(text: str, *, reply_markup: InlineKeyboardMarkup) -> None:
        await _reply_draft_controls(update, data, text, reply_markup=reply_markup)

    if state is WizardState.INCOMING:
        if message is not None:
            await reply(
                "1/7. Что поступило от пациента? Выберите самое близкое описание.",
                reply_markup=_v2_single_keyboard("incomingKind"),
            )
    elif state is WizardState.SITUATION:
        if message is not None:
            selected = data.get("situationAreas")
            await reply(
                "2/7. С чем связана ситуация? Можно выбрать несколько вариантов, "
                "затем «Продолжить».",
                reply_markup=_v2_multi_keyboard(
                    "situationAreas", selected if isinstance(selected, list) else []
                ),
            )
    elif state is WizardState.SERVICES:
        await reply(
            "2/7. Какие услуги затронуты? Укажите от одной до пяти через точку с запятой. "
            "Если точная услуга неизвестна, так и напишите.",
            reply_markup=_v2_keyboard([]),
        )
    elif state is WizardState.EVENT:
        await reply(
            "3/7. Кратко опишите, что произошло, только фактами (10–1500 символов, без ФИО).",
            reply_markup=_v2_keyboard([]),
        )
    elif state is WizardState.EVENT_DATE:
        await reply(
            "Когда пациент обратился по этой ситуации? Укажите дату, например 18.10.2026. "
            "Если не знаете точную дату, так и напишите.",
            reply_markup=_v2_keyboard([]),
        )
    elif state is WizardState.CHRONOLOGY:
        if message is not None:
            await reply(
                "3/7. Это первое обращение по ситуации или конфликт уже продолжается?",
                reply_markup=_v2_single_keyboard("conflictStage"),
            )
    elif state is WizardState.CLINIC_ACTIONS:
        if message is not None:
            selected = data.get("clinicActions")
            await reply(
                "4/7. Что клиника уже сделала? Выберите всё подходящее, затем «Продолжить».",
                reply_markup=_v2_multi_keyboard(
                    "clinicActions", selected if isinstance(selected, list) else []
                ),
            )
    elif state is WizardState.HEALTH:
        if message is not None:
            selected = data.get("healthSignals")
            await reply(
                "5/7. Есть ли известные последствия для здоровья? «Недостаточно сведений» "
                "не означает, что последствий не было.",
                reply_markup=_v2_multi_keyboard(
                    "healthSignals", selected if isinstance(selected, list) else []
                ),
            )
    elif state is WizardState.MATERIALS:
        attached = data.get("caseMaterialsStatus") == "ATTACHED" and not missing_material
        prompt = (
            "6/7. Если есть, приложите обезличенную претензию, переписку или договор — "
            "юрист сможет их посмотреть. Можно продолжить без файла. "
            "Форматы: .txt, .pdf или .docx до 15 МБ. "
            "Файл сохраняется в материалах кейса. "
            "Удалите ФИО, контакты, номера карт, изображения и ненужные медицинские сведения. "
            "Рекомендации готовятся по описанию "
            "и подтверждённым ответам."
        )
        rows = [[("Продолжить без файла", "case:v2:material:skip")]]
        if attached:
            prompt = "6/7. Обезличенный материал добавлен. Можно добавить ещё один или продолжить."
            rows = [[("Продолжить", "case:v2:material:done")]]
        if message is not None:
            await reply(prompt, reply_markup=_v2_keyboard(rows))
    elif state is WizardState.SAFETY:
        question = screening_question(data)
        if question is None:
            await _prompt_v2_draft(update, WizardState.SUMMARY, data)
            return
        field, prompt = question
        rows = [[("Не знаю", f"case:safety:{field}:UNKNOWN")]]
        if field != "amount":
            rows.insert(0, [("Да", f"case:safety:{field}:YES"),
                            ("Нет", f"case:safety:{field}:NO")])
        if known_urgent_report(data):
            rows.append([("Подтвердить известное без других уточнений",
                          "case:safety:known:confirm")])
        await reply(
            prompt + "\nЕсли сведений нет, выберите «Не знаю».",
            reply_markup=_v2_keyboard(rows),
        )
    elif state is WizardState.SUMMARY:
        if message is not None:
            pages = v2_review_pages(data)
            for index, page in enumerate(pages):
                rows = [
                    [("✅ Всё верно", "case:v2:summary:confirm")],
                    [("📎 Добавить файл", "case:v2:material:attach")],
                ] if index == len(pages) - 1 else []
                await reply(page, reply_markup=_v2_keyboard(rows))
    elif state is WizardState.ANALYSIS_CLARIFICATION:
        request = next_clarification_request(data)
        if request is None or known_urgent_report(data):
            await _prompt_v2_draft(update, WizardState.SUMMARY, data)
            return
        slot = "global" if request.service_slot is None else str(request.service_slot)
        prefix = f"case:v2:clarify:{request.question_id}:{slot}:"
        answered = {
            (answer.question_id, answer.service_slot)
            for answer in confirmed_clarification_answers(data).answers
        }
        pending = [item for item in clarification_requests(data)
                   if (item.question_id, item.service_slot) not in answered]
        questions = "\n\n".join(
            f"{index}. {clarification_question(item, data)}"
            for index, item in enumerate(pending, start=1)
        )
        await reply(
            "Уточните недостающие факты:\n\n" + questions
            + "\n\nМожно ответить на все вопросы одним сообщением. "
            "Если сведений нет, напишите, что именно неизвестно. "
            "Кнопки ниже относятся к первому вопросу.",
            reply_markup=_v2_keyboard([
                [(label, prefix + value)] for value, label in clarification_choices(request)
            ]),
        )
    elif state is WizardState.V2_CONFIRM:
        workflow_id = UUID(str(data["workflow_id"]))
        if message is not None:
            await reply(
                ("Фактическая карточка подтверждена. Подготовить рекомендации?"
                 if preparation else
                 "Фактическая карточка подтверждена. Сформировать внутренний отчёт?"),
                reply_markup=confirm_keyboard(workflow_id, preparation=preparation),
            )


async def _advance_v2_draft(
    update: Update,
    data: dict[str, Any],
) -> WizardState:
    state = _v2_state(v2_next_missing_state(data))
    await _prompt_v2_draft(update, state, data)
    return state


async def choose_v2_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    callback = await _answer_callback(update)
    if callback is None:
        return _v2_state(str(_wizard_data(context).get(DRAFT_STATE_KEY, V2_INCOMING)))
    parts = callback.split(":", 4)
    if len(parts) != 5:
        return _v2_state(str(_wizard_data(context).get(DRAFT_STATE_KEY, V2_INCOMING)))
    _, version, action, field, value = parts
    if (version, action) != ("v2", "single"):
        return _v2_state(str(_wizard_data(context).get(DRAFT_STATE_KEY, V2_INCOMING)))
    data = _wizard_data(context)
    try:
        parsed = parse_v2_answer(field, value)
        if field == "incomingKind" and data.get(field) != parsed:
            data.pop("safetyScreening", None)
        data[field] = parsed
    except ValueError:
        await _reply(update, "Выберите вариант кнопкой под текущим вопросом.")
        return _v2_state(str(data.get(DRAFT_STATE_KEY, V2_INCOMING)))
    if field == "incomingKind":
        # A correspondence/file can be supplied on the dedicated material step;
        # do not record a fictional attachment just because the user has one.
        data["incomingSourceStatus"] = "NOT_ATTACHED"
    return await _advance_v2_draft(update, data)


async def choose_v2_multi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    callback = await _answer_callback(update)
    data = _wizard_data(context)
    current = _v2_state(str(data.get(DRAFT_STATE_KEY, V2_SITUATION)))
    if callback is None:
        return current
    parts = callback.split(":", 4)
    if len(parts) != 5:
        return current
    _, version, action, field, value = parts
    if (version, action) != ("v2", "multi") or field not in _V2_MULTI_FIELDS:
        return current
    expected_field = {WizardState.SITUATION: "situationAreas",
                      WizardState.CLINIC_ACTIONS: "clinicActions",
                      WizardState.HEALTH: "healthSignals"}.get(current)
    if field != expected_field:
        return current
    selected = data.get(field)
    values = list(selected) if isinstance(selected, list) else []
    if value == "DONE":
        try:
            data[field] = parse_v2_answer(field, ",".join(values))
        except ValueError:
            await _reply(update, "Выберите хотя бы один вариант, затем нажмите «Продолжить».")
            return current
        if field == "situationAreas" and "TREATMENT" not in data[field]:
            updated = invalidate_service_clarification_answers(data, None)
            data.clear()
            data.update(updated)
        return await _advance_v2_draft(update, data)
    desired = value[:1] if value[:1] in {"+", "-"} else None
    if desired is not None:
        value = value[1:]
        if (desired == "+" and value in values) or (desired == "-" and value not in values):
            return current
    if value in values:
        values.remove(value)
    else:
        values.append(value)
    try:
        data[field] = parse_v2_answer(field, ",".join(values)) if values else []
    except ValueError as exc:
        await _reply(update, str(exc))
        return current
    if field == "situationAreas" and "TREATMENT" not in values:
        updated = invalidate_service_clarification_answers(data, None)
        data.clear()
        data.update(updated)
    if field == "healthSignals":
        data.pop("safetyScreening", None)
    return current


async def record_v2_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = _wizard_data(context)
    state = _v2_state(str(data.get(DRAFT_STATE_KEY, V2_EVENT)))
    field = _V2_TEXT_FIELDS.get(state)
    if field is None:
        return state
    try:
        parsed = parse_v2_answer(field, _message_text(update))
        if field == "affectedServices":
            updated = invalidate_service_clarification_answers(data, cast(list[str], parsed))
            data.clear()
            data.update(updated)
        else:
            data[field] = parsed
    except ValueError as exc:
        await _reply_draft_controls(update, data, str(exc), reply_markup=_v2_keyboard([]))
        return state
    return await _advance_v2_draft(update, data)


async def answer_analysis_clarification(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> int:
    data = _wizard_data(context)
    request = next_clarification_request(data)
    if request is None or known_urgent_report(data):
        return await _advance_v2_draft(update, data)
    callback = await _answer_callback(update)
    slot = "global" if request.service_slot is None else str(request.service_slot)
    prefix = f"case:v2:clarify:{request.question_id}:{slot}:"
    if callback is not None:
        if not callback.startswith(prefix):
            await _reply(update, "Выберите ответ под текущим вопросом.")
            return WizardState.ANALYSIS_CLARIFICATION
        raw = callback.removeprefix(prefix)
        if raw not in dict(clarification_choices(request)):
            await _reply(update, "Выберите ответ под текущим вопросом.")
            return WizardState.ANALYSIS_CLARIFICATION
    else:
        raw = _message_text(update)
    try:
        updated = apply_clarification_answer(data, request, raw)
    except ValueError as exc:
        await _prompt_v2_draft(update, WizardState.ANALYSIS_CLARIFICATION, data)
        await _reply(update, str(exc))
        return WizardState.ANALYSIS_CLARIFICATION
    data.clear()
    data.update(updated)
    return await _advance_v2_draft(update, data)


async def choose_factual_safety(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    callback = await _answer_callback(update)
    data = _wizard_data(context)
    pending = screening_question(data)
    if callback == "case:safety:known:confirm" and known_urgent_report(data):
        explicitly_skip_other_questions(data)
        return await _advance_v2_draft(update, data)
    parts = callback.split(":") if isinstance(callback, str) else []
    if (
        len(parts) != 4 or parts[:2] != ["case", "safety"]
        or pending is None or parts[2] != pending[0]
    ):
        await _prompt_v2_draft(update, WizardState.SAFETY, data)
        return WizardState.SAFETY
    try:
        answer_screening(data, parts[2], parts[3])
    except ValueError:
        await _reply(update, "Выберите ответ под текущим вопросом.")
        return WizardState.SAFETY
    return await _advance_v2_draft(update, data)


async def record_factual_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = _wizard_data(context)
    pending = screening_question(data)
    if pending is None or pending[0] != "amount":
        await _reply(update, "Ответьте кнопкой под текущим вопросом.")
        return WizardState.SAFETY
    try:
        answer_screening(data, "amount", _message_text(update))
    except ValueError:
        await _reply(update, "Укажите положительную сумму в рублях, не более двух знаков "
                     "после запятой, или нажмите «Не знаю».")
        return WizardState.SAFETY
    return await _advance_v2_draft(update, data)


async def v2_material_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    callback = await _answer_callback(update)
    if callback == "case:v2:material:attach":
        data = _wizard_data(context)
        if data.get("caseMaterialsStatus") == "NOT_ATTACHED":
            # Reopen an explicitly skipped upload step without dropping real files.
            data.pop("caseMaterialsStatus")
        await _prompt_v2_draft(update, WizardState.MATERIALS, data)
        return WizardState.MATERIALS
    if callback not in {"case:v2:material:skip", "case:v2:material:done"}:
        return WizardState.MATERIALS
    data = _wizard_data(context)
    if callback.endswith("skip"):
        data["caseMaterialsStatus"] = "NOT_ATTACHED"
        return await _advance_v2_draft(update, data)
    if data.get("caseMaterialsStatus") != "ATTACHED":
        await _reply(
            update,
            "Сначала отправьте обезличенный файл либо выберите продолжение без файла.",
        )
        return WizardState.MATERIALS
    return await _advance_v2_draft(update, data)


async def edit_v2_optional_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    callback = await _answer_callback(update)
    selected = {"case:v2:context:actions": WizardState.CLINIC_ACTIONS,
                "case:v2:context:history": WizardState.CHRONOLOGY}.get(callback or "")
    if selected is None:
        return WizardState.SUMMARY
    await _prompt_v2_draft(update, selected, _wizard_data(context))
    return selected


async def _retire_v2_controls(update: Update) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except (TelegramError, TypeError):
        # A completed submission stays completed if Telegram cannot edit history.
        logger.info("obsolete intake keyboard could not be retired")


async def recover_v2_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Old recap controls can recover the selected draft, never select another one."""
    await _answer_callback(update)
    data = _user_data(context).get(WIZARD_DATA_KEY)
    if not isinstance(data, dict):
        await _retire_v2_controls(update)
        await _reply(update, "Эта карточка завершена или больше не активна. "
                     "Продолжайте текущий диалог по последнему сообщению.")
        return ConversationHandler.END
    try:
        actor_id = _actor_id(update)
        if actor_id is None:
            raise ValueError("draft actor is missing")
        draft_id = UUID(str(data[DRAFT_ID_KEY]))
        current = _saved_draft_selection(
            draft_id, await _legal_core(context).get_intake_draft(draft_id, actor_id),
            is_v2=_is_v2_draft(data),
        )
    except (KeyError, ValueError, LegalCoreApiError):
        _clear_wizard(context)
        await _retire_v2_controls(update)
        await _reply(update, "🔒 Черновик сейчас недоступен. Обновите список через /menu.")
        return ConversationHandler.END
    data.clear()
    data.update(current)
    await _retire_v2_controls(update)
    state = WizardState[str(data[DRAFT_STATE_KEY])]
    await _prompt_resumed_draft(update, state, data)
    return state


async def confirm_v2_summary(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, preparation: bool = False,
) -> int:
    if await _answer_callback(update) != "case:v2:summary:confirm":
        return WizardState.SUMMARY
    data = _wizard_data(context)
    missing = _v2_state(v2_next_missing_state(data))
    if missing is not WizardState.SUMMARY:
        await _reply(update, "Перед подтверждением уточните недостающие сведения.")
        await _prompt_v2_draft(update, missing, data)
        return missing
    await _prompt_v2_draft(update, WizardState.V2_CONFIRM, data, preparation=preparation)
    return WizardState.V2_CONFIRM


async def back_v2_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _answer_callback(update) != "case:v2:back":
        return WizardState.INCOMING
    data = _wizard_data(context)
    current_name = str(data.get(DRAFT_STATE_KEY, V2_INCOMING))
    if current_name in {"CLINIC_ACTIONS", "CHRONOLOGY"}:
        # Checklist changes are provisional, durably staged input. Back exits the
        # optional editor to the one factual recap; it neither confirms a case nor
        # promises to undo a prior saved selection. Empty context means absence.
        review_data = dict(data)
        if review_data.get("clinicActions") == []:
            review_data.pop("clinicActions")
        if v2_next_missing_state(review_data) == WizardState.SUMMARY.name:
            data.clear()
            data.update(review_data)
            await _prompt_v2_draft(update, WizardState.SUMMARY, data)
            return WizardState.SUMMARY
    if current_name == "SAFETY" and previous_screening_question(data):
        await _prompt_v2_draft(update, WizardState.SAFETY, data)
        return WizardState.SAFETY
    if current_name == "ANALYSIS_CLARIFICATION" and previous_clarification_question(data):
        await _prompt_v2_draft(update, WizardState.ANALYSIS_CLARIFICATION, data)
        return WizardState.ANALYSIS_CLARIFICATION
    states = v2_active_states(data)
    try:
        index = states.index(current_name)
    except ValueError:
        return WizardState.INCOMING
    previous = _v2_state(states[max(index - 1, 0)])
    if previous is WizardState.SAFETY and screening_complete(data):
        previous_screening_question(data)
    if previous is WizardState.ANALYSIS_CLARIFICATION:
        previous_clarification_question(data)
    await _prompt_v2_draft(update, previous, data)
    return previous


async def _save_v2_material_state(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    actor_id: int,
    draft_id: UUID,
    revision: int,
    data: dict[str, Any],
    source_status: str,
) -> int:
    client = _legal_core(context)
    # Upload advances the server revision but does not refresh our other facts.
    # Read again after its receipt, then merge only the observed attachment flags.
    current = _saved_draft_selection(
        draft_id, await client.get_intake_draft(draft_id, actor_id),
    )
    current_revision = int(current[DRAFT_REVISION_KEY])
    if current_revision < revision or current.get("caseMaterialsStatus") != "ATTACHED":
        raise ValueError("material receipt does not match current draft")
    if current[DRAFT_STATE_KEY] != WizardState.MATERIALS.name:
        raise LegalCoreApiError(409, "INTAKE_DRAFT_REVISION_CONFLICT", "draft step changed")
    current["caseMaterialsStatus"] = "ATTACHED"
    current["incomingSourceStatus"] = source_status
    payload = _draft_payload(current)
    saved = await client.save_intake_draft(
        draft_id,
        actor_id,
        expected_revision=current_revision,
        wizard_state=WizardState.MATERIALS.name,
        draft_data=payload,
    )
    saved_selection = _saved_draft_selection(draft_id, saved)
    saved_revision = int(saved_selection[DRAFT_REVISION_KEY])
    if saved_revision <= current_revision or _draft_payload(saved_selection) != payload:
        raise ValueError("material state save response is invalid")
    data.clear()
    data.update(saved_selection)
    return saved_revision


async def _reconcile_v2_material(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict[str, Any], *,
    actor_id: int, source_status: str, receipt_confirmed: bool,
) -> int:
    try:
        draft_id = UUID(str(data[DRAFT_ID_KEY]))
        recovered = _saved_draft_selection(
            draft_id, await _legal_core(context).get_intake_draft(draft_id, actor_id),
        )
    except (KeyError, ValueError, LegalCoreApiError):
        _clear_wizard(context)
        await _reply(
            update, "⚠️ Не удалось проверить сохранение материала. Он мог сохраниться. "
            "Откройте /menu → «Мои черновики» перед повтором.",
        )
        return ConversationHandler.END
    data.clear()
    data.update(recovered)
    if receipt_confirmed and data.get("caseMaterialsStatus") == "ATTACHED":
        # The upload itself returned a validated receipt. Keep its observed source
        # kind for the next guarded save even if the secondary state PUT failed.
        data["incomingSourceStatus"] = source_status
    notice = (
        "Материал сохранён. Обновление карточки не удалось подтвердить полностью. "
        if receipt_confirmed
        else "Последнее сохранение материала не удалось подтвердить полностью. "
    )
    await _reply(
        update, "⚠️ " + notice + "Состояние материалов и карточки обновлено с сервера. "
        "Повтор того же файла безопасен; сохранённые материалы остаются в карточке.",
    )
    state = WizardState[str(data[DRAFT_STATE_KEY])]
    await _prompt_v2_draft(update, state, data)
    return state


async def _upload_v2_material(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    content: bytes,
    source_filename: str,
    content_type: str,
    source_status: str,
) -> int:
    actor_id = _actor_id(update)
    data = _wizard_data(context)
    if actor_id is None or not _is_v2_draft(data):
        return ConversationHandler.END
    uploaded: dict[str, Any] | None = None
    receipt_confirmed = False
    try:
        draft_id = UUID(str(data[DRAFT_ID_KEY]))
        uploaded = await _legal_core(context).upload_case_material(
            draft_id,
            actor_id,
            content=content,
            source_filename=source_filename,
            content_type=content_type,
        )
        revision = uploaded.get("draftRevision")
        material = uploaded.get("material")
        if not isinstance(revision, int) or revision < 1 or not isinstance(material, dict):
            raise ValueError("material upload response is invalid")
        UUID(str(material["id"]))
        receipt_confirmed = True
        await _save_v2_material_state(
            context,
            actor_id=actor_id,
            draft_id=draft_id,
            revision=revision,
            data=data,
            source_status=source_status,
        )
    except LegalCoreApiError as exc:
        logger.warning("v2 case material upload rejected: %s", exc.code)
        if uploaded is not None or exc.status_code >= 500:
            return await _reconcile_v2_material(
                update, context, data, actor_id=actor_id, source_status=source_status,
                receipt_confirmed=receipt_confirmed,
            )
        message = (
            "⚠️ Файл не добавлен: в нём обнаружены персональные данные или он не прошёл "
            "проверку формата. Обезличьте его и попробуйте снова."
            if exc.code == "CASE_MATERIAL_DIRECT_IDENTIFIER_NOT_ALLOWED"
            else "⚠️ Файл не добавлен. Проверьте формат, размер и обезличивание, затем повторите."
        )
        await _reply_draft_controls(update, data, message, reply_markup=_v2_keyboard([]))
        return WizardState.MATERIALS
    except (KeyError, ValueError) as exc:
        logger.warning("v2 case material upload response invalid: %s", type(exc).__name__)
        if uploaded is not None:
            return await _reconcile_v2_material(
                update, context, data, actor_id=actor_id, source_status=source_status,
                receipt_confirmed=receipt_confirmed,
            )
        await _reply(update, "⚠️ Не удалось безопасно сохранить материал. Попробуйте позже.")
        return WizardState.MATERIALS
    await _reply(
        update,
        "✅ Обезличенный материал добавлен только к этой карточке. Он не является нормативным "
        "источником и не передаётся модели.",
    )
    await _prompt_v2_draft(update, WizardState.MATERIALS, data)
    return WizardState.MATERIALS


async def _prepare_v2_material_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> int | None:
    data, actor_id = _wizard_data(context), _actor_id(update)
    try:
        if actor_id is None:
            raise ValueError("material actor is missing")
        draft_id = UUID(str(data[DRAFT_ID_KEY]))
        current = _saved_draft_selection(
            draft_id, await _legal_core(context).get_intake_draft(draft_id, actor_id),
        )
    except (KeyError, ValueError, LegalCoreApiError):
        _clear_wizard(context)
        await _reply(update, "🔒 Черновик сейчас недоступен. Обновите список через /menu.")
        return ConversationHandler.END
    state = WizardState[str(current[DRAFT_STATE_KEY])]
    changed = current[DRAFT_REVISION_KEY] != data.get(DRAFT_REVISION_KEY)
    if changed or state is not WizardState.MATERIALS:
        data.clear()
        data.update(current)
        await _prompt_v2_draft(update, state, data)
    # Do not consume this file/text for a different server-owned step.
    return None if state is WizardState.MATERIALS else state


async def receive_v2_material_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    blocked = await _prepare_v2_material_input(update, context)
    if blocked is not None:
        return blocked
    message = update.effective_message
    document = None if message is None else message.document
    if document is None or not document.file_name:
        await _reply(update, "Не удалось определить файл. Поддерживаются .txt, .pdf и .docx.")
        return WizardState.MATERIALS
    if isinstance(document.file_size, int) and document.file_size > _V2_MAX_UPLOAD_BYTES:
        await _reply(update, "Файл больше допустимого лимита 15 МБ.")
        return WizardState.MATERIALS
    source_filename = document.file_name.strip()
    content_type = _V2_FILE_TYPES.get(PurePosixPath(source_filename).suffix.casefold())
    if content_type is None:
        await _reply(update, "Поддерживаются только обезличенные .txt, .pdf и .docx до 15 МБ.")
        return WizardState.MATERIALS
    declared_mime = (document.mime_type or "application/octet-stream").split(";", 1)[0]
    if declared_mime not in {content_type, "application/octet-stream"}:
        await _reply(update, "Расширение файла не совпадает с его MIME-типом.")
        return WizardState.MATERIALS
    await _reply(update, "Проверяю обезличивание и безопасно сохраняю файл…")
    try:
        telegram_file = await context.bot.get_file(document.file_id)
        content = bytes(await telegram_file.download_as_bytearray())
    except TelegramError:
        await _reply(update, "⚠️ Не удалось получить файл из Telegram. Попробуйте ещё раз.")
        return WizardState.MATERIALS
    if not content or len(content) > _V2_MAX_UPLOAD_BYTES:
        await _reply(update, "Файл пустой или превышает лимит 15 МБ.")
        return WizardState.MATERIALS
    return await _upload_v2_material(
        update,
        context,
        content=content,
        source_filename=source_filename,
        content_type=content_type,
        source_status="FILE_ATTACHED",
    )


async def receive_v2_material_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    blocked = await _prepare_v2_material_input(update, context)
    if blocked is not None:
        return blocked
    value = _message_text(update)
    if contains_probable_person_name(value) or pseudonymize_text(value).changed:
        await _reply(
            update,
            "В тексте обнаружены возможные персональные данные. Удалите их и отправьте "
            "обезличенный текст ещё раз.",
        )
        return WizardState.MATERIALS
    try:
        safe_text = parse_v2_answer("eventSummary", value)
    except ValueError as exc:
        await _reply(update, str(exc))
        return WizardState.MATERIALS
    if not isinstance(safe_text, str):  # defensive boundary for future parser changes
        await _reply(update, "Не удалось проверить текст материала.")
        return WizardState.MATERIALS
    return await _upload_v2_material(
        update,
        context,
        content=safe_text.encode("utf-8"),
        source_filename="telegram-message.txt",
        content_type="text/plain",
        source_status="TEXT_ATTACHED",
    )


async def reject_v2_material_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> int:
    del context
    await _reply(
        update,
        "Этот вид сообщения не поддерживается. Отправьте обезличенный файл "
        ".txt, .pdf или .docx до 15 МБ либо выберите продолжение без файла.",
    )
    return WizardState.MATERIALS


def _signal_answer(callback: str, name: str) -> str | None:
    prefix = f"case:{name}:"
    if not callback.startswith(prefix):
        return None
    value = callback.removeprefix(prefix).upper()
    return value if value in {"YES", "NO", "UNKNOWN"} else None


def confirm_keyboard(workflow_id: UUID, *, preparation: bool = False) -> InlineKeyboardMarkup:
    callback = f"case:confirm{'2' if preparation else ''}:{workflow_id}"
    if len(callback.encode()) > 64:
        raise ValueError("Telegram callback is too long")
    label = "✅ Подготовить рекомендации" if preparation else "✅ Сформировать отчёт"
    return _keyboard([[(label, callback), ("❌ Отменить", "case:cancel")]])


_LEGACY_ANALYSIS_DATE_FIELDS = ("claim_date", "incident_date", "service_date")
_DATE_QUESTION_LABELS = {
    "claim_date": "первого обращения пациента",
    "incident_date": "проблемной ситуации",
    "service_date": "оказания услуги",
}


def _date_precision(value: object) -> str | None:
    raw: object
    precision: object
    if isinstance(value, str):
        raw, precision = value, "EXACT"
    elif isinstance(value, dict):
        raw, precision = value.get("date"), value.get("precision")
    else:
        return None
    if not isinstance(raw, str) or precision not in {"EXACT", "APPROXIMATE"}:
        return None
    try:
        date.fromisoformat(raw)
    except ValueError:
        return None
    return precision


def _dates_needing_clarification(data: dict[str, Any]) -> tuple[str, ...]:
    if _is_v2_draft(data):
        return () if _date_precision(data.get("eventDate")) == "EXACT" else ("eventDate",)
    for field in _LEGACY_ANALYSIS_DATE_FIELDS:
        precision = _date_precision(data.get(field))
        if precision == "EXACT":
            return ()
        if precision == "APPROXIMATE":
            return (field,)
    return _LEGACY_ANALYSIS_DATE_FIELDS


def _legacy_answers_complete(data: dict[str, Any]) -> bool:
    try:
        facts_from_draft(_draft_from_data(data))
    except (KeyError, TypeError, ValueError):
        return False
    return True


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


def _draft_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in data.items()
        if key not in {
            "workflow_id", DRAFT_ID_KEY, DRAFT_REVISION_KEY, DRAFT_STATE_KEY,
            "_v2_prompt_message_id",
        }
    }


async def _persist_transition(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, int]],
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    receipt = _CallbackAck()
    acknowledgement = _CALLBACK_ACK_SCOPE.set(receipt)
    try:
        if update.callback_query is not None:
            await _answer_callback(update)
        return await _persist_selected_transition(handler, update, context)
    except Exception as error:
        if receipt.query is update.callback_query and receipt.result is not None:
            # PTB constructs a new error context after this scope has unwound.
            # Carry one exact-query ACK fact with this update's escaping error;
            # preserve its original type for transport failure accounting.
            error.__dict__["_telegram_acknowledged_callback"] = receipt.query
        raise
    finally:
        _CALLBACK_ACK_SCOPE.reset(acknowledgement)


async def _persist_selected_transition(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, int]],
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    data = _wizard_data(context)
    actor_id = _actor_id(update)
    try:
        draft_id = UUID(str(data[DRAFT_ID_KEY]))
        if actor_id is None:
            raise ValueError("draft actor is missing")
        # Rendering cached clinic facts is a read operation too. Core must check
        # the current membership, subscription and exact draft before any output.
        remote = await _legal_core(context).get_intake_draft(draft_id, actor_id)
        latest = _saved_draft_selection(draft_id, remote, is_v2=_is_v2_draft(data))
    except (KeyError, ValueError, LegalCoreApiError):
        _clear_wizard(context)
        await _reply(update, "🔒 Черновик сейчас недоступен. Обновите список через /menu.")
        return ConversationHandler.END
    if latest[DRAFT_REVISION_KEY] != data.get(DRAFT_REVISION_KEY):
        data.clear()
        data.update(latest)
        await _reply(update, "Карточка изменилась на сервере. Обновил сохранённый шаг; "
                     "ответьте на текущий вопрос.")
        state = WizardState[str(data[DRAFT_STATE_KEY])]
        await _prompt_resumed_draft(update, state, data)
        return state
    query = update.callback_query
    if query is not None:
        message = query.message
        expected_message = data.get("_v2_prompt_message_id")
        if (
            type(expected_message) is not int or message is None
            or message.message_id != expected_message
        ):
            if query.data in {"case:v2:context:actions", "case:v2:context:history"}:
                return await recover_v2_context(update, context)
            await _answer_callback(update)
            await _reply(
                update, "Эта карточка устарела. Используйте кнопки последнего вопроса "
                "или откройте текущий черновик заново.",
                reply_markup=_keyboard([[("Открыть текущий черновик", f"case:draft:{draft_id}")]]),
            )
            return WizardState[str(data[DRAFT_STATE_KEY])]
    from telegram_gateway.intake_cleanup import capture, delete_saved
    from telegram_gateway.multi_choice_runtime import render_selection, selection_field

    checklist = selection_field(update, data.get(DRAFT_STATE_KEY))
    completed_step = capture(update, str(draft_id), data.get(DRAFT_REVISION_KEY),
                             str(data.get(DRAFT_STATE_KEY)), data.get("_v2_prompt_message_id"))
    before = _draft_payload(data)
    next_state = await handler(update, context)
    if not isinstance(next_state, WizardState):
        return next_state
    if _draft_payload(data) == before and data.get(DRAFT_STATE_KEY) == next_state.name:
        if checklist is not None:
            await render_selection(update, data,
                                   _v2_multi_keyboard(checklist, data.get(checklist, [])))
        return next_state
    actor_id = _actor_id(update)
    try:
        draft_id = UUID(str(data[DRAFT_ID_KEY]))
        revision = int(data[DRAFT_REVISION_KEY])
        if actor_id is None or revision < 1:
            raise ValueError("draft selection is missing")
        saved = await _legal_core(context).save_intake_draft(
            draft_id,
            actor_id,
            expected_revision=revision,
            wizard_state=next_state.name,
            draft_data=_draft_payload(data),
        )
        saved_revision = saved.get("revision")
        if not isinstance(saved_revision, int) or saved_revision < 1:
            raise ValueError("draft response is invalid")
        if checklist is not None and (
            saved.get("id") != str(draft_id) or saved.get("wizardState") != next_state.name
            or saved.get("draftData") != _draft_payload(data) or saved_revision != revision + 1
        ):
            raise ValueError("checklist persistence receipt mismatch")
        data[DRAFT_REVISION_KEY] = saved_revision
        data[DRAFT_STATE_KEY] = next_state.name
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        logger.warning("intake draft save failed: %s", type(exc).__name__)
        _clear_wizard(context)
        await _reply(
            update,
            "⚠️ Черновик не удалось сохранить. Откройте /menu → «Мои черновики» и попробуйте снова.",
        )
        return ConversationHandler.END
    if checklist is not None:
        await render_selection(update, data, _v2_multi_keyboard(checklist, data.get(checklist, [])))
    elif (saved.get("id") == str(draft_id) and saved.get("wizardState") == next_state.name
            and saved.get("draftData") == _draft_payload(data) and saved_revision == revision + 1):
        await delete_saved(update, context, completed_step, saved,
                           data.get("_v2_prompt_message_id"))
    return next_state


def _persisted(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, int]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, int]]:
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        return await _persist_transition(handler, update, context)

    return wrapped


def _legal_core(context: ContextTypes.DEFAULT_TYPE) -> LegalCoreClient:
    client = context.bot_data.get(LEGAL_CORE_CLIENT_KEY)
    if client is None:
        raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "Legal Core unavailable")
    return cast(LegalCoreClient, client)


async def _menu_actor_role(client: LegalCoreClient, actor_id: int) -> str | None:
    try:
        actor = await client.get_actor(actor_id)
    except (LegalCoreApiError, AttributeError):
        return None
    role = actor.get("role") if isinstance(actor, dict) else None
    return role if isinstance(role, str) else None


async def _menu_legal_editor_status(client: LegalCoreClient, actor_id: int) -> bool:
    try:
        editor_status = await client.get_legal_editor_status(actor_id)
    except (LegalCoreApiError, AttributeError):
        return False
    return isinstance(editor_status, dict) and editor_status.get("isLegalEditor") is True


async def _menu_reference_evaluation_access(client: LegalCoreClient, actor_id: int) -> bool:
    try:
        access = await client.get_reference_evaluation_access(actor_id)
    except (LegalCoreApiError, AttributeError):
        return False
    return isinstance(access, dict) and (
        access.get("canContribute") is True or access.get("canReview") is True
    )


async def _main_menu_for_actor(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE | None,
) -> InlineKeyboardMarkup:
    """Use a role-aware surface when Legal Core is reachable; otherwise stay usable."""

    actor_id = _actor_id(update)
    if context is None or actor_id is None:
        return main_menu_keyboard()
    try:
        client = _legal_core(context)
    except (LegalCoreApiError, AttributeError):
        return main_menu_keyboard()
    role, is_legal_editor, has_reference_evaluation_access = await asyncio.gather(
        _menu_actor_role(client, actor_id),
        _menu_legal_editor_status(client, actor_id),
        _menu_reference_evaluation_access(client, actor_id),
    )
    keyboard = main_menu_keyboard(role, is_legal_editor=is_legal_editor)
    if not has_reference_evaluation_access:
        return keyboard
    return InlineKeyboardMarkup(
        [
            *keyboard.inline_keyboard,
            [InlineKeyboardButton("🗂 Эталонные кейсы", callback_data="refeval:open")],
        ]
    )


def _actor_id(update: Update) -> int | None:
    return None if update.effective_user is None else update.effective_user.id


@dataclass
class _CallbackAck:
    query: object | None = None
    result: str | None = None


_CALLBACK_ACK_SCOPE: ContextVar[_CallbackAck | None] = ContextVar(
    "telegram_callback_ack_scope", default=None,
)


async def _answer_callback(update: Update) -> str | None:
    query = update.callback_query
    if query is None or not isinstance(query.data, str):
        return None
    acknowledgement = _CALLBACK_ACK_SCOPE.get()
    if acknowledgement is not None and acknowledgement.query is query:
        return acknowledgement.result
    result: str | None = query.data
    try:
        await query.answer()
    except BadRequest:
        # Callback queries expire quickly in Telegram. An update may still be
        # delivered after a short network interruption, but it must not turn
        # into a failed handler or block newer updates from being processed.
        logger.info("ignored an expired Telegram callback")
        result = None
    if acknowledgement is not None:
        acknowledgement.query = query
        acknowledgement.result = result
    return result


async def case_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _answer_callback(update)
    _clear_pending_inputs(context)
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

    if actor.get("role") not in CASE_INTAKE_ROLES:
        await _reply(update, "⚠️ Legal Core вернул некорректный ответ. Попробуйте позже.")
        return ConversationHandler.END
    try:
        draft = await _legal_core(context).create_intake_draft(
            actor_id, intake_schema_version=V2_INTAKE_SCHEMA_VERSION
        )
        draft_id = UUID(str(draft["id"]))
        revision = draft.get("revision")
        draft_data = draft.get("draftData")
        if (
            draft.get("wizardState") != V2_INCOMING
            or not isinstance(revision, int)
            or revision < 1
            or not isinstance(draft_data, dict)
            or draft_data.get("intakeVersion") != 2
        ):
            raise ValueError("draft response is invalid")
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        logger.warning("intake draft create failed: %s", type(exc).__name__)
        await _reply(update, "⚠️ Не удалось открыть черновик. Попробуйте ещё раз позже.")
        return ConversationHandler.END
    data = dict(draft_data)
    data.update(
        {
            "workflow_id": str(draft_id),
            DRAFT_ID_KEY: str(draft_id),
            DRAFT_REVISION_KEY: revision,
            DRAFT_STATE_KEY: WizardState.INCOMING.name,
        }
    )
    _user_data(context)[WIZARD_DATA_KEY] = data
    await _reply(
        update,
        "📝 Новая карточка открыта. Кейс будет создан после вашей проверки.\n\n"
        "Указывайте только обезличенные сведения — без ФИО, телефона, адреса, "
        "номера карты и неанонимизированных файлов пациента.",
        reply_markup=back_keyboard(),
    )
    await _prompt_v2_draft(update, WizardState.INCOMING, data)
    return WizardState.INCOMING


_DRAFT_INCIDENT_LABELS = {
    "QUALITY_COMPLAINT": "Качество лечения",
    "PAYMENT_DISPUTE": "Оплата / возврат",
    "INFORMED_CONSENT": "Согласие и документы",
    "PERSONAL_DATA": "Персональные данные",
    "OTHER": "Другая ситуация",
}


def _draft_updated_label(value: object) -> str:
    """Render the server timestamp compactly without exposing draft contents."""

    if not isinstance(value, str) or len(value) < 16:
        return "время неизвестно"
    return value[8:10] + "." + value[5:7] + " " + value[11:16]


def _draft_delete_callback_data(draft_id: UUID, *, confirm: bool = False) -> str:
    prefix = _DRAFT_DELETE_CONFIRM_PREFIX if confirm else "case:draft:delete:"
    callback_data = f"{prefix}{draft_id}"
    if len(callback_data.encode()) > 64:
        raise ValueError("Telegram draft deletion callback is too long")
    return callback_data


def _draft_delete_confirmation_keyboard(draft_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🗑 Да, удалить",
                    callback_data=_draft_delete_callback_data(draft_id, confirm=True),
                )
            ],
            [InlineKeyboardButton("← К черновикам", callback_data="case:drafts")],
            *back_keyboard().inline_keyboard,
        ]
    )


async def delete_intake_draft_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    callback_data = await _answer_callback(update)
    if callback_data is None:
        return
    match = _DRAFT_DELETE_CALLBACK_RE.fullmatch(callback_data)
    actor_id = _actor_id(update)
    query = update.callback_query
    if match is None or actor_id is None or query is None:
        return
    try:
        draft_id = UUID(match.group(1))
    except ValueError:
        return

    if not callback_data.startswith(_DRAFT_DELETE_CONFIRM_PREFIX):
        await query.edit_message_text(
            "🗑 УДАЛИТЬ ЧЕРНОВИК?\n\n"
            "Он сразу исчезнет из активного списка. Закрытая запись сохранится только на "
            "срок, предусмотренный политикой хранения, после чего будет очищена."
            "\n\nЭто действие нельзя отменить.",
            reply_markup=_draft_delete_confirmation_keyboard(draft_id),
        )
        return

    try:
        current = await _legal_core(context).get_intake_draft(draft_id, actor_id)
        revision = current.get("revision")
        if not isinstance(revision, int) or revision < 1:
            raise ValueError("draft response is invalid")
        archived = await _legal_core(context).archive_intake_draft(
            draft_id,
            actor_id,
            expected_revision=revision,
        )
        archived_revision = archived.get("revision")
        if not isinstance(archived_revision, int) or archived_revision <= revision:
            raise ValueError("archive response is invalid")
    except LegalCoreApiError as exc:
        logger.warning("intake draft archive failed: %s", exc.code)
        if exc.status_code in {404, 409}:
            message = "⚠️ Черновик уже изменён или удалён. Обновите список черновиков."
        else:
            message = "⚠️ Не удалось удалить черновик. Попробуйте ещё раз позже."
        await query.edit_message_text(message, reply_markup=back_keyboard())
        return
    except ValueError:
        logger.warning("intake draft archive returned invalid data")
        await query.edit_message_text(
            "⚠️ Не удалось безопасно удалить черновик. Обновите список и попробуйте снова.",
            reply_markup=back_keyboard(),
        )
        return

    wizard_data = _user_data(context).get(WIZARD_DATA_KEY)
    if isinstance(wizard_data, dict) and wizard_data.get(DRAFT_ID_KEY) == str(draft_id):
        _clear_wizard(context)
    await query.edit_message_text(
        "🗑 Черновик удалён из активного списка.", reply_markup=back_keyboard()
    )


async def show_intake_drafts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _answer_callback(update) != "case:drafts":
        return
    actor_id = _actor_id(update)
    if actor_id is None:
        await _reply(update, "Не удалось определить администратора.")
        return
    try:
        response = await _legal_core(context).list_intake_drafts(actor_id)
        items = response.get("items")
        if not isinstance(items, list):
            raise ValueError("draft list response is invalid")
    except (LegalCoreApiError, ValueError) as exc:
        logger.warning("intake draft list failed: %s", type(exc).__name__)
        await _reply(update, "⚠️ Не удалось загрузить черновики. Попробуйте позже.")
        return
    if not items:
        await _reply(
            update,
            "📂 Активных черновиков нет. Нажмите «Создать кейс», чтобы открыть новый.",
            reply_markup=back_keyboard(),
        )
        return
    rows: list[list[InlineKeyboardButton]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        try:
            draft_id = UUID(str(item["id"]))
            revision = int(item["revision"])
        except (KeyError, TypeError, ValueError):
            continue
        if revision < 1:
            continue
        incident_type = item.get("incidentType")
        label = _DRAFT_INCIDENT_LABELS.get(
            incident_type if isinstance(incident_type, str) else "", "Новая карточка"
        )
        state = item.get("wizardState")
        step = state if isinstance(state, str) else "СОХРАНЁННЫЙ ШАГ"
        button_label = f"{index}. {label} · {step}"[:42]
        rows.append(
            [
                InlineKeyboardButton(
                    f"{button_label}\n{_draft_updated_label(item.get('updatedAt'))}",
                    callback_data=f"case:draft:{draft_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    "🗑 Удалить черновик",
                    callback_data=_draft_delete_callback_data(draft_id),
                )
            ]
        )
    if not rows:
        await _reply(update, "⚠️ Не удалось прочитать список черновиков. Попробуйте позже.")
        return
    rows.append([InlineKeyboardButton("← Главное меню", callback_data="menu")])
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "📂 МОИ ЧЕРНОВИКИ\n\nВыберите карточку для продолжения. Данные сохраняются 30 дней.",
            reply_markup=InlineKeyboardMarkup(rows),
        )


async def _prompt_resumed_draft(update: Update, state: WizardState, data: dict[str, Any]) -> None:
    message = update.effective_message
    if _is_v2_draft(data):
        await _prompt_v2_draft(update, state, data)
    elif state == WizardState.INCIDENT:
        if message is not None:
            await _reply_draft_controls(
                update, data, "Выберите основной тип ситуации:", reply_markup=INCIDENT_KEYBOARD
            )
    elif state == WizardState.SERVICE_TYPE:
        await _reply(update, "1/10. Какая стоматологическая услуга была оказана? Кратко, без ФИО.")
    elif state == WizardState.SERVICE_DATE:
        await _reply(update, "2/10. Дата оказания услуги в формате ГГГГ-ММ-ДД:")
    elif state == WizardState.INCIDENT_DATE:
        await _reply(update, "3/10. Когда произошла проблемная ситуация? ГГГГ-ММ-ДД:")
    elif state == WizardState.CLAIM_DATE:
        await _reply(update, "4/10. Когда пациент впервые обратился с претензией? ГГГГ-ММ-ДД:")
    elif state == WizardState.PROBLEM_SUMMARY:
        await _reply(
            update, "5/10. Опишите ситуацию нейтрально и по фактам, без ФИО (10–1500 символов)."
        )
    elif state == WizardState.PATIENT_DEMAND:
        if message is not None:
            await _reply_draft_controls(
                update, data, "6/10. Чего требует пациент?", reply_markup=DEMAND_KEYBOARD,
            )
    elif state == WizardState.DEMAND_AMOUNT:
        await _reply(update, "Укажите требуемую сумму в рублях, только число:")
    elif state == WizardState.FORMAL_CLAIM:
        await _ask_formal_claim(update, data)
    elif state == WizardState.CLAIM_RECEIVED_AT:
        await _reply(update, "Дата получения письменной претензии, ГГГГ-ММ-ДД:")
    elif state == WizardState.CLAIM_DEADLINE:
        await _reply(update, "Срок ответа из документа, ГГГГ-ММ-ДД:")
    elif state == WizardState.HARM:
        await _ask_harm(update, data)
    elif state == WizardState.HOSPITALIZATION:
        if message is not None:
            await _reply_draft_controls(
                update, data, "Была госпитализация?", reply_markup=YES_NO_KEYBOARDS["hospital"]
            )
    elif state == WizardState.LAWYER:
        await _ask_lawyer(update, data)
    elif state == WizardState.REPRESENTATIVE_AUTHORITY:
        if message is not None:
            await _reply_draft_controls(
                update, data, "Подтверждены полномочия представителя?",
                reply_markup=YES_NO_KEYBOARDS["representative"],
            )
    elif state == WizardState.LAWYER_DEADLINE:
        await _reply(update, "Срок ответа представителю, ГГГГ-ММ-ДД или «неизвестно»:")
    elif state == WizardState.AUTHORITY:
        await _ask_authority(update, data)
    elif state == WizardState.AUTHORITY_KIND:
        await _reply(update, "Какой орган или суд направил документ? Без персональных данных.")
    elif state == WizardState.AUTHORITY_DATE:
        await _reply(update, "Дата документа органа, ГГГГ-ММ-ДД:")
    elif state == WizardState.AUTHORITY_DEADLINE:
        await _reply(update, "Срок ответа из документа, ГГГГ-ММ-ДД:")
    elif state == WizardState.REGULATOR_THREAT:
        await _ask_regulator_threat(update, data)
    elif state == WizardState.DOCUMENTS:
        await _ask_documents(update, data)
    else:
        workflow_id = UUID(str(data["workflow_id"]))
        if message is not None:
            await _reply_draft_controls(
                update, data, "Карточка заполнена. Сформировать единый PDF-отчёт?",
                reply_markup=confirm_keyboard(workflow_id),
            )


async def resume_intake_draft(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, preparation: bool = False,
    draft_id: UUID | None = None,
) -> int:
    direct_resume = draft_id is not None
    callback = await _answer_callback(update)
    _clear_pending_admin_grant(context)
    actor_id = _actor_id(update)
    if callback is None or actor_id is None:
        return ConversationHandler.END
    try:
        if draft_id is None:
            draft_id = UUID(callback.removeprefix("case:draft:"))
        elif not isinstance(draft_id, UUID):
            raise ValueError("direct resume requires a validated draft identity")
        draft = await _legal_core(context).get_intake_draft(draft_id, actor_id)
        state_name = draft.get("wizardState")
        revision = draft.get("revision")
        draft_data = draft.get("draftData")
        if (
            not isinstance(state_name, str)
            or not isinstance(revision, int)
            or revision < 1
            or not isinstance(draft_data, dict)
        ):
            raise ValueError("draft response is invalid")
        state = WizardState[state_name]
        if direct_resume:
            _saved_draft_selection(draft_id, draft, is_v2=draft_data.get("intakeVersion") == 2)
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        logger.warning("intake draft resume failed: %s", type(exc).__name__)
        _clear_wizard(context)
        await _reply(update, "⚠️ Этот черновик недоступен. Обновите список через /menu.")
        return ConversationHandler.END
    _clear_wizard(context)
    _clear_pending_inputs(context)
    data = dict(draft_data)
    pending_materials = (state is WizardState.MATERIALS
                         and data.get("caseMaterialsStatus") != "NOT_ATTACHED")
    # A valid value may be an unfinished checklist selection or a prefilled edit.
    # DONE/accepted single answers persist the next state, so the persisted open
    # optional checkpoint remains authoritative regardless of value completeness.
    pending_context = state in {WizardState.CLINIC_ACTIONS, WizardState.CHRONOLOGY}
    if data.get("intakeVersion") == 2 and not pending_materials and not pending_context and (
        preparation or state in {
            WizardState.SUMMARY, WizardState.V2_CONFIRM, WizardState.ANALYSIS_CLARIFICATION,
        }
    ):
        # Navigation checkpoints are not missing facts. Explicit back/edit still
        # opens its selected field; generic resume asks only what is absent.
        state = _v2_state(v2_next_missing_state(data))
    data.update(
        {
            "workflow_id": str(draft_id),
            DRAFT_ID_KEY: str(draft_id),
            DRAFT_REVISION_KEY: revision,
            DRAFT_STATE_KEY: state.name,
        }
    )
    _user_data(context)[WIZARD_DATA_KEY] = data
    if not (direct_resume and context.bot_data.get("intake_simplified_dialogue") is True):
        await _reply(update, "✅ Черновик открыт. Продолжаем с сохранённого шага.")
    await _prompt_resumed_draft(update, state, data)
    return state


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
    data = _wizard_data(context)
    if field in _LEGACY_ANALYSIS_DATE_FIELDS and _legacy_answers_complete(data):
        if value["precision"] != "EXACT":
            await _reply(
                update,
                "Для подбора применимой редакции нужна точная дата. "
                "Если её пока нет, /cancel сохранит черновик.",
            )
            return current
        data[field] = value
        await _reply_draft_controls(
            update, data,
            f"Дата {_DATE_QUESTION_LABELS[field]}: {value['date']}. "
            "Остальные сведения не изменены. Сформировать внутренний отчёт?",
            reply_markup=confirm_keyboard(UUID(str(data["workflow_id"]))),
        )
        return WizardState.CONFIRM
    data[field] = value
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
        await _reply_draft_controls(
            update, _wizard_data(context), "6/10. Чего требует пациент?",
            reply_markup=DEMAND_KEYBOARD,
        )
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
    await _ask_formal_claim(update, _wizard_data(context))
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
    await _ask_formal_claim(update, _wizard_data(context))
    return WizardState.FORMAL_CLAIM


async def _ask_formal_claim(update: Update, data: dict[str, Any]) -> None:
    await _reply_draft_controls(update, data, "7/10. Поступила письменная претензия?",
                             reply_markup=YES_NO_KEYBOARDS["formal"])


async def choose_formal_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    formal = None if data is None else _signal_answer(data, "formal")
    if formal is None:
        return WizardState.FORMAL_CLAIM
    _wizard_data(context)["formal_claim"] = formal
    if formal == "YES":
        await _reply(update, "Дата получения письменной претензии, ГГГГ-ММ-ДД:")
        return WizardState.CLAIM_RECEIVED_AT
    await _ask_harm(update, _wizard_data(context))
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
        await _reply_draft_controls(
            update, _wizard_data(context), "Выберите вариант:",
            reply_markup=YES_NO_KEYBOARDS["harm"],
        )
    return result


async def _ask_harm(update: Update, data: dict[str, Any]) -> None:
    await _reply_draft_controls(update, data, "8/10. Заявляет ли пациент о вреде здоровью?",
                             reply_markup=YES_NO_KEYBOARDS["harm"])


async def choose_harm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    harm = None if data is None else _signal_answer(data, "harm")
    if harm is None:
        return WizardState.HARM
    _wizard_data(context)["harm_claimed"] = harm
    if harm in {"YES", "UNKNOWN"}:
        message = update.effective_message
        if message is not None:
            await _reply_draft_controls(
                update, _wizard_data(context), "Была госпитализация?",
                reply_markup=YES_NO_KEYBOARDS["hospital"]
            )
        return WizardState.HOSPITALIZATION
    await _ask_lawyer(update, _wizard_data(context))
    return WizardState.LAWYER


async def choose_hospitalization(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    hospitalization = None if data is None else _signal_answer(data, "hospital")
    if hospitalization is None:
        return WizardState.HOSPITALIZATION
    _wizard_data(context)["hospitalization"] = hospitalization
    await _ask_lawyer(update, _wizard_data(context))
    return WizardState.LAWYER


async def _ask_lawyer(update: Update, data: dict[str, Any]) -> None:
    await _reply_draft_controls(
        update, data, "9/12. Связывался ли с клиникой представитель или юрист пациента?",
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
            await _reply_draft_controls(
                update, _wizard_data(context), "Подтверждены полномочия представителя?",
                reply_markup=YES_NO_KEYBOARDS["representative"],
            )
        return WizardState.REPRESENTATIVE_AUTHORITY
    await _ask_authority(update, _wizard_data(context))
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
        await _reply_draft_controls(
            update, _wizard_data(context), "Выберите вариант:",
            reply_markup=YES_NO_KEYBOARDS["authority"],
        )
    return WizardState.AUTHORITY


async def _ask_authority(update: Update, data: dict[str, Any]) -> None:
    await _reply_draft_controls(
        update, data, "10/12. Есть обращение в суд или контролирующий орган?",
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
    await _ask_regulator_threat(update, _wizard_data(context))
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
    await _ask_regulator_threat(update, _wizard_data(context))
    return WizardState.REGULATOR_THREAT


async def _ask_regulator_threat(update: Update, data: dict[str, Any]) -> None:
    await _reply_draft_controls(
        update, data, "11/12. Есть угроза обращения в суд или контролирующий орган?",
        reply_markup=YES_NO_KEYBOARDS["regulator_threat"],
    )


async def choose_regulator_threat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = await _answer_callback(update)
    threat = None if data is None else _signal_answer(data, "regulator_threat")
    if threat is None:
        return WizardState.REGULATOR_THREAT
    _wizard_data(context)["regulator_threat"] = threat
    await _ask_documents(update, _wizard_data(context))
    return WizardState.DOCUMENTS


async def _ask_documents(update: Update, data: dict[str, Any]) -> None:
    await _reply_draft_controls(
        update, data, "12/12. Есть договор, медкарта и информированное согласие?\n"
        "Сами файлы сюда не загружайте.", reply_markup=DOCUMENTS_KEYBOARD,
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
        await _reply_draft_controls(
            update, data, "Сформировать единый PDF-отчёт?",
            reply_markup=confirm_keyboard(workflow_id),
        )
    return WizardState.CONFIRM


async def _send_workflow_report(
    update: Update,
    client: LegalCoreClient,
    workflow: dict[str, Any],
    actor_id: int,
) -> None:
    if "receiptVersion" in workflow:
        text, keyboard = preparation_workflow_message(workflow)
        await _reply(update, text, reply_markup=keyboard)
        return
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
    early_escalation_id = case.get("earlyEscalationId")
    if early_escalation_id is not None:
        escalation_id = UUID(str(early_escalation_id))
        await _reply(
            update,
            "⚖️ Карточка уже передана юристу. Правовой анализ выполняется отдельно.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                "Открыть карточку юриста", callback_data=f"case:escalation:{escalation_id}")],
                *back_keyboard().inline_keyboard]),
        )
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
        workflow_id, preparation = decode_workflow_confirmation(callback)
        client = _legal_core(context)
        workflow = await client.get_workflow(workflow_id, actor_id)
        if preparation and workflow.get("receiptVersion") != 2:
            raise ValueError("workflow preparation mode mismatch")
        await _send_workflow_report(update, client, workflow, actor_id)
    except (ValueError, LegalCoreApiError, TelegramError) as exc:
        logger.warning("workflow recovery failed: %s", type(exc).__name__)
        if isinstance(exc, LegalCoreApiError) and exc.status_code == 404:
            await _reply(
                update,
                "Эта карточка ещё не была подтверждена. "
                "Откройте /menu → «Мои черновики», продолжите сохранённую карточку "
                "и подтвердите её. Если черновик удалён, создайте новый.",
            )
        elif isinstance(exc, LegalCoreApiError) and exc.status_code == 403:
            await _reply(update, "🔒 Доступ администратора отозван.")
        else:
            await _reply(update, "⚠️ Не удалось получить отчёт. Попробуйте ещё раз.")


async def choose_case_date_to_clarify(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    callback = await _answer_callback(update)
    data = _wizard_data(context)
    field = "" if callback is None else callback.removeprefix("case:clarify-date:")
    if field not in _dates_needing_clarification(data) or field not in _DATE_QUESTION_LABELS:
        return WizardState.CONFIRM
    state = {
        "claim_date": WizardState.CLAIM_DATE,
        "incident_date": WizardState.INCIDENT_DATE,
        "service_date": WizardState.SERVICE_DATE,
    }[field]
    await _reply(
        update,
        f"Уточните точную дату {_DATE_QUESTION_LABELS[field]}: ГГГГ-ММ-ДД. "
        "Если даты пока нет, /cancel сохранит черновик.",
    )
    return state


async def _has_confirmed_workflow(
    client: LegalCoreClient, workflow_id: UUID, actor_id: int, *, preparation: bool,
) -> bool:
    """A promoted attachment belongs to a case; only its existing workflow may replay."""
    try:
        wire = await client.get_workflow(workflow_id, actor_id)
        receipt = (TelegramPreparationReceipt if preparation else TelegramWorkflowResponse
                   ).model_validate(wire)
        return (receipt.workflow_id == workflow_id
                and receipt.case.intake_schema_version == V2_INTAKE_SCHEMA_VERSION)
    except (LegalCoreApiError, TypeError, ValueError):
        return False


async def confirm_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    callback = await _answer_callback(update)
    if callback == "case:v2:summary:confirm" and context.bot_data.get(
        "intake_simplified_dialogue"
    ) is True:
        # The current summary's explicit click authorizes both submission and
        # analysis. Existing source-message/revision checks run in _persisted.
        callback = f"case:confirm2:{_wizard_data(context).get('workflow_id')}"
    if callback == "case:cancel":
        return await cancel_case(update, context)
    if callback is None or not callback.startswith(("case:confirm:", "case:confirm2:")):
        data = _wizard_data(context)
        return WizardState.V2_CONFIRM if _is_v2_draft(data) else WizardState.CONFIRM
    actor_id = _actor_id(update)
    data = _wizard_data(context)
    preparation = callback.startswith("case:confirm2:")
    if actor_id is None:
        _clear_wizard(context)
        return ConversationHandler.END
    try:
        workflow_id, preparation = decode_workflow_confirmation(callback)
        if preparation and not _is_v2_draft(data):
            raise ValueError("preparation requires a v2 draft")
        if workflow_id != UUID(str(data["workflow_id"])):
            raise ValueError("workflow callback does not match active conversation")
        date_questions = _dates_needing_clarification(data)
        if date_questions and not preparation and not (
            _is_v2_draft(data) and known_urgent_report(data)
        ):
            if _is_v2_draft(data):
                await _reply(
                    update,
                    "До подтверждения кейса нужна точная дата события. Черновик сохранён.",
                )
                await _prompt_v2_draft(update, WizardState.EVENT_DATE, data)
                return WizardState.EVENT_DATE
            rows = [
                [(f"📅 Дата {_DATE_QUESTION_LABELS[field]}", f"case:clarify-date:{field}")]
                for field in date_questions
            ]
            rows.append([("💾 Сохранить черновик", "case:cancel")])
            await _reply_draft_controls(
                update, data,
                "Для анализа нужна точная дата. Выберите, какую можете уточнить. "
                "До этого кейс не будет подтверждён; черновик сохранён.",
                reply_markup=_keyboard(rows),
            )
            return WizardState.CONFIRM
        if _is_v2_draft(data):
            missing = _v2_state(v2_next_missing_state(data))
            if missing is not WizardState.SUMMARY:
                await _reply(update, "Перед подтверждением уточните недостающие факты. "
                             "Черновик сохранён.")
                await _prompt_v2_draft(update, missing, data)
                return missing
        client = _legal_core(context)
        if _is_v2_draft(data):
            if data.get("caseMaterialsStatus") == "ATTACHED":
                try:
                    materials = CaseMaterialListResponse.model_validate(await client._json_request(
                        "GET", f"/v1/telegram-intake-drafts/{workflow_id}/case-materials",
                        telegram_user_id=actor_id,
                    ))
                except (LegalCoreApiError, TypeError, ValueError) as error:
                    # After a lost successful submit, files have moved to the case
                    # and the source draft is closed. Reuse only a real owned receipt;
                    # the idempotent submit below still validates the exact payload.
                    replay = (isinstance(error, LegalCoreApiError) and error.status_code == 404
                              and await _has_confirmed_workflow(
                                  client, workflow_id, actor_id, preparation=preparation,
                              ))
                    if not replay:
                        await _reply(update, "Не удалось проверить приложенные файлы. "
                                     "Повторите подтверждение или откройте «Добавить файл».")
                        await _prompt_v2_draft(update, WizardState.SUMMARY, data)
                        return WizardState.SUMMARY
                    materials = None
                if materials is not None and not materials.items:
                    await _reply(update, "Приложенный файл не найден или срок его хранения истёк. "
                                 "Прикрепите его ещё раз либо продолжите без файла.")
                    await _prompt_v2_draft(update, WizardState.MATERIALS, data,
                                           missing_material=True)
                    return WizardState.MATERIALS
            facts = facts_from_v2_data(_draft_payload(data))
            if preparation:
                workflow = await client.submit_workflow(
                    workflow_id, facts, actor_id,
                    intake_schema_version=V2_INTAKE_SCHEMA_VERSION, receipt_version=2,
                )
            else:
                workflow = await client.submit_workflow(
                    workflow_id, facts, actor_id, intake_schema_version=V2_INTAKE_SCHEMA_VERSION,
                )
        else:
            facts = facts_from_draft(_draft_from_data(data))
            workflow = await client.submit_workflow(workflow_id, facts, actor_id)
        if preparation and workflow.get("receiptVersion") != 2:
            raise ValueError("workflow preparation mode mismatch")
        if preparation and context.bot_data.get("intake_simplified_dialogue") is True:
            from telegram_gateway.dialogue_submission import present_submission

            await present_submission(update, context, workflow)
        else:
            await _send_workflow_report(update, client, workflow, actor_id)
    except TelegramError as exc:
        logger.warning("case report delivery failed: %s", type(exc).__name__)
        await _reply(
            update,
            ("Сведения сохранены, но Telegram не доставил сообщение. "
             "Нажмите «Подготовить рекомендации» ещё раз — повторный кейс не создаётся.")
            if preparation else
            ("✅ Карточка сохранена, но Telegram не доставил отчёт. "
             "Нажмите «Сформировать отчёт» ещё раз — повторный кейс не создаётся."),
        )
        return WizardState.V2_CONFIRM if _is_v2_draft(data) else WizardState.CONFIRM
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        if isinstance(exc, LegalCoreApiError) and exc.status_code == 403:
            _clear_wizard(context)
            await _reply(update, "🔒 Доступ администратора отозван. Обратитесь к владельцу бота.")
            return ConversationHandler.END
        logger.warning("case report generation failed: %s", type(exc).__name__)
        await _reply(
            update,
            ("⚠️ Подготовку пока не удалось подтвердить. Повторите «Подготовить рекомендации» "
             "или /cancel. Черновик не удалён.") if preparation else
            "⚠️ Отчёт пока не сформирован. Нажмите «Сформировать отчёт» ещё раз или /cancel.",
        )
        return WizardState.V2_CONFIRM if _is_v2_draft(data) else WizardState.CONFIRM

    try:
        await client.archive_intake_draft(
            UUID(str(data[DRAFT_ID_KEY])),
            actor_id,
            expected_revision=int(data[DRAFT_REVISION_KEY]),
        )
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        logger.warning("intake draft archive after submission failed: %s", type(exc).__name__)
        await _reply(
            update,
            ("Сведения сохранены. Черновик временно остался в списке; "
             "повторное подтверждение безопасно.") if preparation else
            ("Отчёт сформирован. Черновик временно остался в списке; "
             "повторное подтверждение безопасно."),
        )

    if _is_v2_draft(data):
        await _retire_v2_controls(update)
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
        "Сбор приостановлен, черновик сохранён. Откройте /menu → «Мои черновики», "
        "чтобы продолжить или переключиться на другой.",
    )
    return ConversationHandler.END


async def timeout_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_wizard(context)
    await _reply(
        update,
        "Время заполнения истекло. Черновик сохранён: откройте /menu → «Мои черновики».",
    )
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
    if context is not None and _user_data(context).get(ADMIN_GRANT_ACCESS_KEY) is True:
        await record_admin_grant_access(update, context)
        return
    if context is not None and _user_data(context).get(ADMIN_GRANT_PILOT_KEY) is True:
        await record_admin_grant_pilot(update, context)
        return
    if context is not None and _user_data(context).get(TEAM_MEMBER_ROLE_KEY) in {
        "CLINIC_ADMIN",
        "CLINIC_LAWYER",
    }:
        await record_team_member(update, context)
        return
    await _reply(update, TEXT_INPUT_DISABLED_MESSAGE)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
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
    if callback_data == "menu":
        _clear_pending_inputs(context)
        caption = START_MESSAGE
        keyboard = await _main_menu_for_actor(update, context)
    elif callback_data == "team:open":
        await clinic_team(update, context)
        return
    elif callback_data == "case:escalations":
        caption = (
            "⚖️ ЭСКАЛАЦИИ HIGH/CRITICAL\n\n"
            "Заявки с уровнями HIGH и CRITICAL направляются юристу; "
            "внешний юридически значимый ответ автоматически не отправляется."
        )
        keyboard = back_keyboard()
    elif callback_data == "account:id":
        actor_id = _actor_id(update)
        caption = (
            "👤 ВАШ TELEGRAM ID\n\n"
            f"{actor_id if actor_id is not None else 'Не удалось определить'}\n\n"
            "Передайте этот ID владельцу сервиса только для подключения доступа."
        )
        keyboard = back_keyboard()
    else:
        caption = HELP_MESSAGE if callback_data == "help" else SCREENS[callback_data]
        keyboard = back_keyboard()
    try:
        message = query.message
        if message is not None and getattr(message, "caption", None) is None:
            await query.edit_message_text(text=caption, reply_markup=keyboard)
        else:
            await query.edit_message_caption(caption=caption, reply_markup=keyboard)
    except BadRequest as exc:
        # A double tap can arrive after the first callback has already rendered
        # this exact screen. Telegram rejects that no-op; the user is already
        # on the requested screen, so it must not turn into a failed update.
        if "message is not modified" not in str(exc).lower():
            raise


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
            ),
            legal_editor_gateway_key=load_legal_editor_gateway_key(),
        )
    # post_init runs only after python-telegram-bot completed Bot.initialize/getMe.
    _record_telegram_api_success(application)
    READY_FILE.touch(mode=0o600)
    POLLING_HEARTBEAT_FILE.touch(mode=0o600)
    if application.job_queue is None:
        logger.warning("Telegram polling watchdog is unavailable because job queue is disabled")
    else:
        application.job_queue.run_repeating(
            _polling_watchdog,
            interval=POLLING_WATCHDOG_INTERVAL_SECONDS,
            first=POLLING_WATCHDOG_INTERVAL_SECONDS,
            name="telegram-polling-watchdog",
        )
    logger.info("telegram gateway initialized")


async def on_shutdown(application: TelegramApplication) -> None:
    bot_data = getattr(application, "bot_data", {})
    client = bot_data.pop(LEGAL_CORE_CLIENT_KEY, None)
    if isinstance(client, LegalCoreClient):
        await client.aclose()
    READY_FILE.unlink(missing_ok=True)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = getattr(update, "callback_query", None)
    logger.error(
        "telegram update failed: error=%s has_callback=%s",
        type(context.error).__name__,
        query is not None,
    )
    if isinstance(context.error, (NetworkError, TimedOut)):
        application = getattr(context, "application", None)
        if application is not None:
            failures = _record_telegram_api_failure(application)
            if failures == TELEGRAM_API_FAILURE_LIMIT:
                _mark_telegram_transport_unready(failures=failures)
    if query is None or getattr(context.error, "_telegram_acknowledged_callback", None) is query:
        return
    try:
        await query.answer(
            CALLBACK_ERROR_MESSAGE,
            show_alert=True,
        )
    except Exception:  # The callback can already be expired or answered by Telegram.
        logger.warning("failed to notify user about a callback error")


def build_application(token: str, *, proxy_url: str | None = None) -> TelegramApplication:
    from telegram_gateway.clarification_runtime import create_clarification

    # Application.builder + async handlers follows the official v22.8 pattern.
    # Source: https://docs.python-telegram-bot.org/en/v22.8/examples.echobot.html
    builder = (
        Application.builder()
        .token(token)
        .concurrent_updates(
            ActorSerialUpdateProcessor(
                TELEGRAM_MAX_CONCURRENT_ACTORS,
                queued_update_limit=TELEGRAM_QUEUED_UPDATE_LIMIT,
            )
        )
        .connection_pool_size(TELEGRAM_BOT_API_POOL_SIZE)
        .pool_timeout(TELEGRAM_POOL_TIMEOUT_SECONDS)
        .get_updates_connection_pool_size(TELEGRAM_POLLING_POOL_SIZE)
        .get_updates_pool_timeout(TELEGRAM_POOL_TIMEOUT_SECONDS)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
    )
    configured_proxy_url = load_telegram_proxy_url() if proxy_url is None else proxy_url
    if configured_proxy_url is not None:
        builder = builder.proxy(configured_proxy_url).get_updates_proxy(configured_proxy_url)
    application = builder.build()
    application.add_handler(TypeHandler(Update, _record_update_heartbeat), group=-100)
    application.add_handler(TypeHandler(Update, _require_private_chat), group=-99)
    application.add_handler(TypeHandler(Update, _route_input_mode), group=-90)
    application.add_handler(CallbackQueryHandler(
        create_clarification,
        pattern=r"^case:(?:clarify:(?:[A-Za-z0-9_-]{43}|[0-9a-f-]{36})|clarify2:[A-Za-z0-9_-]{43})$",
    ), group=-2)
    application.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(case_start, pattern=r"^case:start$"),
                CallbackQueryHandler(
                    resume_intake_draft,
                    pattern=(
                        r"^case:draft:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                        r"[0-9a-f]{4}-[0-9a-f]{12}$"
                    ),
                ),
            ],
            states={
                WizardState.INCOMING: [
                    CallbackQueryHandler(
                        _persisted(choose_v2_single),
                        pattern=r"^case:v2:single:incomingKind:",
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.SITUATION: [
                    CallbackQueryHandler(
                        _persisted(choose_v2_multi),
                        pattern=r"^case:v2:multi:situationAreas:",
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.SERVICES: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        _persisted(record_v2_text),
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.EVENT: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        _persisted(record_v2_text),
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.EVENT_DATE: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        _persisted(record_v2_text),
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.CHRONOLOGY: [
                    CallbackQueryHandler(
                        _persisted(choose_v2_single),
                        pattern=r"^case:v2:single:conflictStage:",
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.CLINIC_ACTIONS: [
                    CallbackQueryHandler(
                        _persisted(choose_v2_multi),
                        pattern=r"^case:v2:multi:clinicActions:",
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.HEALTH: [
                    CallbackQueryHandler(
                        _persisted(choose_v2_multi),
                        pattern=r"^case:v2:multi:healthSignals:",
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.MATERIALS: [
                    CallbackQueryHandler(
                        _persisted(v2_material_action), pattern=r"^case:v2:material:"
                    ),
                    MessageHandler(filters.Document.ALL, receive_v2_material_document),
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, receive_v2_material_text
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                    MessageHandler(~filters.TEXT & ~filters.Document.ALL, reject_v2_material_media),
                ],
                WizardState.SUMMARY: [
                    CallbackQueryHandler(
                        _persisted(edit_v2_optional_context),
                        pattern=r"^case:v2:context:(actions|history)$",
                    ),
                    CallbackQueryHandler(
                        _persisted(v2_material_action), pattern=r"^case:v2:material:attach$",
                    ),
                    CallbackQueryHandler(
                        _persisted(confirm_v2_summary),
                        pattern=r"^case:v2:summary:confirm$",
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.SAFETY: [
                    CallbackQueryHandler(
                        _persisted(choose_factual_safety), pattern=r"^case:safety:"
                    ),
                    MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   _persisted(record_factual_amount)),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.ANALYSIS_CLARIFICATION: [
                    CallbackQueryHandler(
                        _persisted(answer_analysis_clarification), pattern=r"^case:v2:clarify:"
                    ),
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, _persisted(answer_analysis_clarification)
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.V2_CONFIRM: [
                    CallbackQueryHandler(
                        _persisted(confirm_case),
                        pattern=(
                            r"^(case:cancel|case:confirm2?:"
                            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                            r"[0-9a-f]{4}-[0-9a-f]{12})$"
                        ),
                    ),
                    CallbackQueryHandler(_persisted(back_v2_draft), pattern=r"^case:v2:back$"),
                ],
                WizardState.INCIDENT: [
                    CallbackQueryHandler(_persisted(choose_incident), pattern=r"^case:incident:")
                ],
                WizardState.SERVICE_TYPE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, _persisted(record_service_type))
                ],
                WizardState.SERVICE_DATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, _persisted(record_service_date))
                ],
                WizardState.INCIDENT_DATE: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, _persisted(record_incident_date)
                    )
                ],
                WizardState.CLAIM_DATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, _persisted(record_claim_date))
                ],
                WizardState.PROBLEM_SUMMARY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, _persisted(record_summary))
                ],
                WizardState.PATIENT_DEMAND: [
                    CallbackQueryHandler(_persisted(choose_demand), pattern=r"^case:demand:")
                ],
                WizardState.DEMAND_AMOUNT: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, _persisted(record_demand_amount)
                    )
                ],
                WizardState.FORMAL_CLAIM: [
                    CallbackQueryHandler(_persisted(choose_formal_claim), pattern=r"^case:formal:")
                ],
                WizardState.CLAIM_RECEIVED_AT: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, _persisted(record_claim_received)
                    )
                ],
                WizardState.CLAIM_DEADLINE: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, _persisted(record_claim_deadline)
                    )
                ],
                WizardState.HARM: [
                    CallbackQueryHandler(_persisted(choose_harm), pattern=r"^case:harm:")
                ],
                WizardState.HOSPITALIZATION: [
                    CallbackQueryHandler(
                        _persisted(choose_hospitalization), pattern=r"^case:hospital:"
                    )
                ],
                WizardState.LAWYER: [
                    CallbackQueryHandler(_persisted(choose_lawyer), pattern=r"^case:lawyer:")
                ],
                WizardState.REPRESENTATIVE_AUTHORITY: [
                    CallbackQueryHandler(
                        _persisted(choose_representative_authority),
                        pattern=r"^case:representative:",
                    )
                ],
                WizardState.LAWYER_DEADLINE: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, _persisted(record_lawyer_deadline)
                    )
                ],
                WizardState.AUTHORITY: [
                    CallbackQueryHandler(_persisted(choose_authority), pattern=r"^case:authority:")
                ],
                WizardState.AUTHORITY_KIND: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, _persisted(record_authority_kind)
                    )
                ],
                WizardState.AUTHORITY_DATE: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, _persisted(record_authority_date)
                    )
                ],
                WizardState.AUTHORITY_DEADLINE: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, _persisted(record_authority_deadline)
                    )
                ],
                WizardState.REGULATOR_THREAT: [
                    CallbackQueryHandler(
                        _persisted(choose_regulator_threat), pattern=r"^case:regulator_threat:"
                    )
                ],
                WizardState.DOCUMENTS: [
                    CallbackQueryHandler(_persisted(choose_documents), pattern=r"^case:documents:")
                ],
                WizardState.CONFIRM: [
                    CallbackQueryHandler(
                        _persisted(confirm_case),
                        pattern=(
                            r"^(case:cancel|case:confirm:"
                            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                            r"[0-9a-f]{4}-[0-9a-f]{12})$"
                        ),
                    ),
                    CallbackQueryHandler(
                        _persisted(choose_case_date_to_clarify),
                        pattern=r"^case:clarify-date:(claim_date|incident_date|service_date)$",
                    ),
                ],
                ConversationHandler.TIMEOUT: [
                    MessageHandler(filters.ALL, timeout_case),
                    CallbackQueryHandler(timeout_case),
                ],
            },
            fallbacks=[
                CallbackQueryHandler(
                    recover_v2_context, pattern=r"^case:v2:context:(actions|history)$",
                ),
                CommandHandler("cancel", cancel_case),
                CommandHandler(["start", "menu"], exit_to_menu),
                CallbackQueryHandler(
                    exit_to_menu_callback,
                    pattern=r"^(menu|features|workflow|privacy|about|account:id|help)$",
                ),
            ],
            allow_reentry=True,
            conversation_timeout=15 * 60,
            # The guided-intake extension locates this durable conversation by
            # name; retain the established identity while v1 drafts exist.
            name="administrator-case-intake-v1",
        )
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("team", clinic_team))
    application.add_handler(CommandHandler("grant_access", grant_access))
    application.add_handler(CommandHandler("grant_pilot", grant_pilot))
    application.add_handler(
        CallbackQueryHandler(prompt_admin_grant_access, pattern=r"^admin:grant$")
    )
    application.add_handler(
        CallbackQueryHandler(prompt_admin_grant_pilot, pattern=r"^admin:pilot$")
    )
    application.add_handler(
        CallbackQueryHandler(prompt_team_member, pattern=r"^team:add:(admin|lawyer)$")
    )
    application.add_handler(
        CallbackQueryHandler(
            resume_workflow,
            pattern=(
                r"^case:confirm2?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}$"
            ),
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            delete_intake_draft_callback,
            pattern=(
                r"^case:draft:delete(?::confirm)?:[0-9a-f]{8}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
            ),
        )
    )
    application.add_handler(CallbackQueryHandler(show_intake_drafts, pattern=r"^case:drafts$"))
    application.add_handler(CallbackQueryHandler(
        recover_v2_context, pattern=r"^case:v2:context:(actions|history)$",
    ))
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
