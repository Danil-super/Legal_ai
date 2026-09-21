# ruff: noqa: RUF001
"""Compose a sparse, explicitly confirmed intake on the existing durable wizard.

Only callbacks in the newly built application are replaced. No module-level monkey
patch, alternative database, new legal policy or background conversation mutation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any, cast
from uuid import UUID, uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from telegram_gateway import bot as gateway_bot
from telegram_gateway import quick_intake_runtime as quick
from telegram_gateway.case_wizard import INTAKE_SCHEMA_VERSION, DateFactValue, LegalCoreApiError
from telegram_gateway.escalation_workspace import _chunks
from telegram_gateway.intake_experience import (
    FIELDS,
    PREFIXES,
    confirmed_candidates,
    drop_field,
    next_missing_state,
    parse_answer,
    review_blocks,
)
from telegram_gateway.quick_intake import QuickIntakeError, QuickIntakePrivacyError
from telegram_gateway.ui import back_keyboard

logger = logging.getLogger(__name__)
Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, int]]


def _pending(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    value = gateway_bot._user_data(context).get(quick._QUICK_CANDIDATE_KEY)
    return value if isinstance(value, dict) and value.get("experienceVersion") == 1 else None


def _review_keyboard(pending: dict[str, Any]) -> InlineKeyboardMarkup:
    prefix = f"quick:review:{pending['nonce']}:"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Всё верно — продолжить", callback_data=prefix + "accept")],
        [InlineKeyboardButton("✏️ Исправить поле", callback_data=prefix + "edit")],
        [InlineKeyboardButton("Отменить", callback_data="quick:cancel")],
        *back_keyboard().inline_keyboard,
    ])


async def _show_review(update: Update, pending: dict[str, Any]) -> None:
    messages = _chunks(review_blocks(pending["candidate_data"]))
    for index, message in enumerate(messages):
        await gateway_bot._reply(
            update, message,
            reply_markup=_review_keyboard(pending) if index == len(messages) - 1 else None,
        )


async def receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = gateway_bot._user_data(context)
    if not data.get(quick._QUICK_PENDING_KEY) or gateway_bot.WIZARD_DATA_KEY in data:
        return
    chat = update.effective_chat
    if chat is None or chat.type != "private":
        raise ApplicationHandlerStop
    try:
        candidates = confirmed_candidates(gateway_bot._message_text(update))
    except QuickIntakePrivacyError:
        await gateway_bot._reply(update, "Уберите ФИО и персональные сведения. "
                                 "Пришлите обезличенное описание заново.")
        raise ApplicationHandlerStop from None
    except QuickIntakeError:
        await gateway_bot._reply(update, "Нужно описание от 10 до 1500 символов.")
        raise ApplicationHandlerStop from None
    pending: dict[str, Any] = {
        "experienceVersion": 1, "nonce": uuid4().hex[:12], "candidate_data": candidates,
        "createKey": str(uuid4()), "saveKey": str(uuid4()), "submitted": False,
    }
    data[quick._QUICK_CANDIDATE_KEY] = pending
    data.pop(quick._QUICK_PENDING_KEY, None)
    await _show_review(update, pending)
    raise ApplicationHandlerStop


async def review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback = await gateway_bot._answer_callback(update)
    actor = gateway_bot._actor_id(update)
    pending = _pending(context)
    if callback is None or actor is None:
        raise ApplicationHandlerStop
    parts = callback.split(":")
    if len(parts) < 4 or pending is None or parts[2] != pending.get("nonce"):
        await gateway_bot._reply(update, "Эта карточка уже изменена или сохранена. "
                                 "Используйте последнюю карточку либо /describe_case.")
        raise ApplicationHandlerStop
    if gateway_bot.WIZARD_DATA_KEY in gateway_bot._user_data(context):
        await gateway_bot._reply(update, "Сначала завершите текущий черновик или откройте /menu.")
        raise ApplicationHandlerStop
    action = parts[3]
    candidates = pending["candidate_data"]
    if pending["submitted"] and action != "accept":
        await gateway_bot._reply(update, "Сохранение уже началось. Повторите «Всё верно», "
                                 "чтобы получить результат того же сохранения.")
        raise ApplicationHandlerStop
    if action == "edit":
        rows = []
        seen: set[str] = set()
        for field, label in FIELDS.values():
            if field in candidates and field not in seen:
                rows.append([InlineKeyboardButton(
                    label, callback_data=f"quick:review:{pending['nonce']}:drop:{field}",
                )])
                seen.add(field)
        rows.extend(list(row) for row in back_keyboard().inline_keyboard)
        await gateway_bot._reply(
            update, "Выберите неверное поле. Его и зависимые сведения уточним отдельно; "
            "остальное сохранится. Изменение исходного описания сбрасывает все его кандидаты.",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    elif action == "drop" and len(parts) == 5 and parts[4] in candidates:
        if parts[4] == "problem_summary":
            candidates.clear()
        else:
            drop_field(candidates, parts[4])
        pending["nonce"] = uuid4().hex[:12]
        await _show_review(update, pending)
    elif action == "accept" and len(parts) == 4:
        pending["submitted"] = True
        client = gateway_bot._legal_core(context)
        state = next_missing_state(candidates)
        try:
            # Stable keys across ambiguous network retries avoid duplicate drafts. Both
            # existing endpoints independently reauthorize the actor and clinic.
            created = await client._json_request(
                "POST", "/v1/telegram-intake-drafts", telegram_user_id=actor,
                idempotency_key=UUID(pending["createKey"]), payload={},
            )
            draft_id = UUID(str(created["id"]))
            revision = created["revision"]
            if type(revision) is not int or revision < 1:
                raise ValueError("invalid draft revision")
            saved = await client._json_request(
                "PUT", f"/v1/telegram-intake-drafts/{draft_id}", telegram_user_id=actor,
                idempotency_key=UUID(pending["saveKey"]), payload={
                    "expectedRevision": revision, "wizardState": state, "draftData": candidates,
                },
            )
            if (saved.get("id") != str(draft_id) or saved.get("wizardState") != state
                    or type(saved.get("revision")) is not int or saved["revision"] <= revision):
                raise ValueError("invalid saved draft")
        except (KeyError, TypeError, ValueError, LegalCoreApiError):
            logger.warning("guided intake save unavailable")
            await gateway_bot._reply(
                update, "Не удалось подтвердить сохранение. Нажмите «Всё верно» повторно: "
                "бот проверит то же сохранение, не создавая новый запрос. "
                "Также можно открыть /menu → «Мои черновики».",
                reply_markup=_review_keyboard(pending),
            )
            raise ApplicationHandlerStop from None
        quick._clear_quick(context)
        await gateway_bot._reply(
            update, "✅ Подтверждённые сведения сохранены, в том числе после пропущенных дат. "
            "Продолжим только с недостающего вопроса. "
            "Перед созданием кейса будет итоговая проверка.",
            reply_markup=quick._continue_keyboard(draft_id),
        )
    raise ApplicationHandlerStop


async def _ask(update: Update, state: gateway_bot.WizardState, data: dict[str, Any]) -> None:
    if state == gateway_bot.WizardState.CONFIRM:
        for text in _chunks(review_blocks(gateway_bot._draft_payload(data), final=True)):
            await gateway_bot._reply(update, text)
    await gateway_bot._prompt_resumed_draft(update, state, data)


def _step_handler(state: gateway_bot.WizardState) -> Handler:
    async def record(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        field = FIELDS[state.name][0]
        if state.name in PREFIXES:
            callback = await gateway_bot._answer_callback(update)
            prefix = PREFIXES[state.name]
            if callback is None or not callback.startswith(prefix):
                return state
            raw = callback.removeprefix(prefix)
        else:
            raw = gateway_bot._message_text(update)
        try:
            value = parse_answer(field, raw)
        except ValueError as exc:
            # parse_answer emits fixed validation copy, never raw data or tool errors.
            await gateway_bot._reply(update, str(exc))
            return state
        data = gateway_bot._wizard_data(context)
        if state.name in {"LAWYER_DEADLINE", "AUTHORITY_DEADLINE"}:
            value, changed = gateway_bot._earliest_known_deadline(
                data.get(field), cast(DateFactValue, value),
            )
            if changed:
                await gateway_bot._reply(
                    update, "У обращений разные сроки. В карточке сохранён ближайший "
                    "из указанных вами; это не расчёт срока по закону.",
                )
        if field in data and data[field] != value:
            drop_field(data, field)
        data[field] = value
        following = gateway_bot.WizardState[next_missing_state(data, after=state.name)]
        await _ask(update, following, data)
        return following

    return gateway_bot._persisted(record)


def install_guided_intake(application: gateway_bot.TelegramApplication) -> None:
    """Keep original filters, timeout, fallbacks and final server submission unchanged."""
    found = False
    for handlers in application.handlers.values():
        for handler in handlers:
            if isinstance(handler, ConversationHandler) and handler.name == (
                "administrator-case-intake-v1"
            ):
                found = True
                for name in FIELDS:
                    state = gateway_bot.WizardState[name]
                    for step in handler.states[state]:
                        step.callback = _step_handler(state)
    if not found:
        raise RuntimeError("durable intake conversation missing")
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, receive_description,
    ), group=-7)
    application.add_handler(CallbackQueryHandler(
        review_callback, pattern=r"^quick:review:[0-9a-f]{12}:(?:accept|edit|drop:[a-z_]+)$",
    ), group=-7)
    # Keep a concrete assertion near composition so future schema changes are reviewed.
    if INTAKE_SCHEMA_VERSION != "dental-case-intake.v1":
        raise RuntimeError("review sparse intake compatibility with the new schema")
