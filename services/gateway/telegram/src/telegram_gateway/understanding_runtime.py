# ruff: noqa: RUF001
"""Short Telegram actions for the Core-owned durable understanding operation."""

from typing import Any
from uuid import UUID

from legal_core.intake_preparation_contracts import PreparationStatusResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    TypeHandler,
)

from telegram_gateway import bot, guided_intake_runtime, quick_intake_runtime
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.escalation_workspace import _chunks
from telegram_gateway.guided_case_intake_v2 import parse_answer
from telegram_gateway.understanding_client import UnderstandingClient, validated_draft
from telegram_gateway.understanding_intent import bound_callback, parse_bound_callback
from telegram_gateway.understanding_review import proposal_blocks
from telegram_gateway.understanding_source import save_source

_SELECTED = "understanding_intake"
_EDIT = "understanding_edit_draft"


def _controls(operation: UUID, draft_id: UUID, *, proposed: bool = False) -> InlineKeyboardMarkup:
    actions = ([("✅ Всё верно — продолжить", "accept")] if proposed else []) + [
        ("Проверить результат", "refresh"), ("Заполнить вручную", "manual"),
        ("Исправить описание", "edit"), ("Отменить разбор", "cancel"),
    ]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=bound_callback(action, operation, draft_id))]
        for label, action in actions
    ] + [[InlineKeyboardButton("← Главное меню", callback_data=bound_callback(
        "menu", operation, draft_id,
    ))]])


async def _checked(
    core: Any, actor: int, operation: UUID, draft_id: UUID,
) -> tuple[PreparationStatusResponse, dict[str, Any]]:
    client = UnderstandingClient(core, actor)
    draft = validated_draft(await core.get_intake_draft(draft_id, actor), draft_id)
    status = await client.get(operation)
    binding = status.source or status.acceptance
    if binding is not None:
        if binding.draft_id != draft_id:
            raise ValueError("operation belongs to a different draft")
        if status.acceptance is not None and draft["revision"] < (
            status.acceptance.accepted_revision
        ):
            # Consent can commit between the two reads. Refresh once, never
            # announce success against an older or impossible draft snapshot.
            draft = validated_draft(await core.get_intake_draft(draft_id, actor), draft_id)
            if draft["revision"] < status.acceptance.accepted_revision:
                raise ValueError("accepted revision is not present in the saved draft")
    else:
        discovered = await client.discover(draft_id)
        if discovered is None or discovered.operation_id != operation:
            raise ValueError("operation no longer belongs to current source")
    if status.state == "PROPOSED":
        await client.proposal_draft(status, draft)
    return status, draft


async def _show(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    status: PreparationStatusResponse, draft: dict[str, Any],
) -> None:
    draft_id = UUID(draft["id"])
    data = bot._user_data(context)
    binding = status.source or status.acceptance
    if binding is not None and binding.draft_id != draft_id:
        raise ValueError("receipt draft mismatch")
    if status.acceptance is not None and draft["revision"] < status.acceptance.accepted_revision:
        raise ValueError("accepted revision is not present in the saved draft")
    if status.state == "PROPOSED":
        actor = bot._actor_id(update)
        if actor is None:
            raise ValueError("actor required")
        await UnderstandingClient(bot._legal_core(context), actor).proposal_draft(status, draft)
    if status.state in {"ACCEPTED", "MANUAL_CONTINUATION"}:
        data.pop(_SELECTED, None)
        await bot._reply(
            update, "Сведения сохранены. Продолжим с недостающих вопросов. "
            "Перед созданием кейса вы подтвердите итоговую фабулу.",
            reply_markup=quick_intake_runtime._continue_keyboard(draft_id),
        )
        return
    data[_SELECTED] = {"draftId": str(draft_id), "operationId": str(status.operation_id)}
    if status.state == "PENDING":
        await bot._reply(
            update, "Описание сохранено. Разбираем сведения из сообщения. "
            "Можно проверить результат или продолжить анкету вручную, не дожидаясь разбора.",
            reply_markup=_controls(status.operation_id, draft_id),
        )
        return
    if status.suggestions is None:
        raise ValueError("proposal missing")
    pages = _chunks(proposal_blocks(draft["draftData"], status.suggestions))
    for index, text in enumerate(pages):
        await bot._reply(
            update, text, reply_markup=_controls(status.operation_id, draft_id, proposed=True)
            if index == len(pages) - 1 else None,
        )


async def receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = bot._user_data(context)
    if not data.get(quick_intake_runtime._QUICK_PENDING_KEY) or bot.WIZARD_DATA_KEY in data:
        return
    actor, chat, message = bot._actor_id(update), update.effective_chat, update.effective_message
    if actor is None or chat is None or message is None or chat.type != "private":
        raise ApplicationHandlerStop
    try:
        summary = parse_answer("eventSummary", bot._message_text(update))
        if not isinstance(summary, str):
            raise ValueError("description required")
        edit = data.get(_EDIT)
        draft = await save_source(
            bot._legal_core(context), actor, chat.id, message.message_id, summary,
            edit_draft_id=UUID(edit) if isinstance(edit, str) else None,
        )
    except (ValueError, LegalCoreApiError):
        await bot._reply(update, "Не удалось подтвердить сохранение описания. "
                         "Проверьте обезличивание и откройте /menu → «Мои черновики». "
                         "Сохранённый черновик не удалён.")
        raise ApplicationHandlerStop from None
    quick_intake_runtime._clear_quick(context)
    data.pop(_EDIT, None)
    draft_id = UUID(draft["id"])
    client = UnderstandingClient(bot._legal_core(context), actor)
    try:
        status = await client.discover(draft_id)
        if status is None:
            status = await client.start(draft_id, draft["revision"])
        await _show(update, context, status, draft)
    except (KeyError, TypeError, ValueError, LegalCoreApiError):
        # Source is durable; a lost start response is recovered by draft discovery, not rerun.
        await bot._reply(
            update, "Описание сохранено. Результат разбора пока недоступен. "
            "Откройте черновик: бот проверит сохранённый результат или продолжит анкету.",
            reply_markup=quick_intake_runtime._continue_keyboard(draft_id),
        )
    raise ApplicationHandlerStop


async def controls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    value = await bot._answer_callback(update)
    actor = bot._actor_id(update)
    if value is None or actor is None:
        raise ApplicationHandlerStop
    operation = draft_id = None
    try:
        action, operation, draft_id = parse_bound_callback(value)
        core = bot._legal_core(context)
        client = UnderstandingClient(core, actor)
        status, draft = await _checked(core, actor, operation, draft_id)
        if action == "accept":
            if status.state == "PROPOSED":
                draft = await client.accept(status)
                status = await client.get(operation)
            if status.state != "ACCEPTED":
                raise ValueError("proposal no longer available")
            await _show(update, context, status, draft)
        elif action in {"manual", "cancel", "edit", "menu"}:
            if status.state == "ACCEPTED":
                if action not in {"manual", "menu"}:
                    raise ValueError("confirmed facts cannot be cancelled")
            elif status.state != "MANUAL_CONTINUATION":
                await client.cancel(operation)  # fence BEFORE any RAM reset or source edit
            data = bot._user_data(context)
            data.pop(_SELECTED, None)
            data.pop(_EDIT, None)
            quick_intake_runtime._clear_quick(context)
            if action == "menu":
                bot._clear_wizard(context)
                await bot.menu(update, context)
            elif action == "edit":
                if set(draft["draftData"]) - {
                    "intakeVersion", "eventSummary", "incomingSourceStatus",
                }:
                    raise ValueError("use ordinary form to correct confirmed fields")
                data[_EDIT] = str(draft_id)
                data[quick_intake_runtime._QUICK_PENDING_KEY] = True
                await bot._reply(
                    update, "Пришлите исправленное обезличенное описание. "
                    "Оно заменит описание в том же черновике.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                        "← Главное меню", callback_data=bound_callback("menu", operation, draft_id),
                    )]]),
                )
            else:
                await bot._reply(
                    update, "Разбор остановлен. Описание и ответы сохранены в черновике."
                    if status.state != "ACCEPTED" else "Подтверждённые сведения сохранены.",
                    reply_markup=quick_intake_runtime._continue_keyboard(draft_id),
                )
        else:
            await _show(update, context, status, draft)
    except (KeyError, TypeError, ValueError, LegalCoreApiError):
        await bot._reply(
            update, "Не удалось подтвердить результат действия. Ничего не считаем "
            "отменённым или подтверждённым до проверки. Проверьте результат либо "
            "откройте текущий черновик через /menu.",
            reply_markup=_controls(operation, draft_id)
            if operation is not None and draft_id is not None else None,
        )
    raise ApplicationHandlerStop


async def fence_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback = update.callback_query.data if update.callback_query is not None else None
    text = bot._message_text(update) if update.callback_query is None else ""
    if isinstance(callback, str) and callback.startswith(
        ("understanding:", "case:draft:", "analysis:pdf:", "esc:pdf:")
    ):
        return
    if not isinstance(callback, str) and not text.startswith("/"):
        return
    data = bot._user_data(context)
    selected = data.get(_SELECTED)
    if not isinstance(selected, dict):
        data.pop(_EDIT, None)
        return
    actor = bot._actor_id(update)
    if actor is None:
        raise ApplicationHandlerStop
    try:
        draft_id, operation = UUID(selected["draftId"]), UUID(selected["operationId"])
        core = bot._legal_core(context)
        status, _draft = await _checked(core, actor, operation, draft_id)
        if status.state in {"PENDING", "PROPOSED"}:
            await UnderstandingClient(core, actor).cancel(operation)
    except (KeyError, TypeError, ValueError, LegalCoreApiError):
        await bot._answer_callback(update)
        await bot._reply(update, "Не удалось подтвердить остановку разбора. "
                         "Сначала проверьте результат последней карточки; черновик сохранён.")
        raise ApplicationHandlerStop from None
    data.pop(_SELECTED, None)
    data.pop(_EDIT, None)


def install_understanding(application: bot.TelegramApplication) -> None:
    """Explicit opt-in composition; no global monkeypatch or background PTB tasks."""
    async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        value = await bot._answer_callback(update)
        actor = bot._actor_id(update)
        if value is None or actor is None:
            return ConversationHandler.END
        try:
            draft_id = UUID(value.removeprefix("case:draft:"))
            core = bot._legal_core(context)
            draft = validated_draft(await core.get_intake_draft(draft_id, actor), draft_id)
            status = await UnderstandingClient(core, actor).discover(draft_id)
            if status is not None and status.state in {"PENDING", "PROPOSED"}:
                status, draft = await _checked(core, actor, status.operation_id, draft_id)
                bot._clear_wizard(context)
                await _show(update, context, status, draft)
                return ConversationHandler.END
        except (KeyError, TypeError, ValueError, LegalCoreApiError):
            await bot._reply(update, "Черновик или результат разбора сейчас недоступен. "
                             "Обновите список через /menu.")
            return ConversationHandler.END
        return await bot.resume_intake_draft(update, context, preparation=True)

    for handlers in application.handlers.values():
        for handler in handlers:
            if getattr(handler, "callback", None) is guided_intake_runtime.receive_description:
                handler.callback = receive_description
            if isinstance(handler, ConversationHandler):
                for entry in handler.entry_points:
                    if entry.callback is bot.resume_intake_draft:
                        entry.callback = resume
    application.add_handler(TypeHandler(Update, fence_navigation), group=-95)
    application.add_handler(CallbackQueryHandler(controls, pattern=r"^understanding:"), group=-7)
