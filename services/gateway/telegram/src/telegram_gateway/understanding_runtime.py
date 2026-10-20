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
from telegram_gateway.dialogue_staging import ENABLED, stage
from telegram_gateway.escalation_workspace import _chunks
from telegram_gateway.guided_case_intake_v2 import parse_answer
from telegram_gateway.intake_delivery import stop, watch
from telegram_gateway.understanding_client import UnderstandingClient, validated_draft
from telegram_gateway.understanding_intent import bound_callback, parse_bound_callback
from telegram_gateway.understanding_review import proposal_blocks
from telegram_gateway.understanding_source import save_source

_SELECTED = "understanding_intake"
_EDIT = "understanding_edit_draft"


def _controls(operation: UUID, draft_id: UUID, *, proposed: bool = False,
              simplified: bool = False) -> InlineKeyboardMarkup:
    actions = ([
        ("✅ Всё верно — продолжить", "accept"), ("Исправить описание", "edit"),
    ] if proposed else ([
        # Waiting is not a choice between two intake branches.
    ] if simplified else [
        ("Проверить результат", "refresh"), ("Продолжить без разбора", "manual"),
        ("Исправить описание", "edit"),
    ]))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=bound_callback(action, operation, draft_id))]
        for label, action in actions
    ] + [[InlineKeyboardButton("Отменить и выйти в меню" if simplified else "← Главное меню",
                              callback_data=bound_callback(
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


async def _superseded(core: Any, actor: int, draft_id: UUID) -> bool:
    """A current owned draft with no current intent can safely leave an old selector.

    For example, staging may have committed while both POST replies were lost.
    The old proposal is then deliberately stale, not a still-running operation
    that needs cancelling. Transport/auth failures never establish this proof.
    """
    validated_draft(await core.get_intake_draft(draft_id, actor), draft_id)
    return await UnderstandingClient(core, actor).discover(draft_id) is None


async def _show(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    status: PreparationStatusResponse, draft: dict[str, Any],
) -> None:
    draft_id = UUID(draft["id"])
    data = bot._user_data(context)
    if status.state != "PENDING":
        stop(update, context, _SELECTED)
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
    simplified = context.bot_data.get(ENABLED) is True
    if status.state in {"ACCEPTED", "MANUAL_CONTINUATION"} and not simplified:
        data.pop(_SELECTED, None)
        await bot._reply(
            update, "Сведения сохранены. Продолжим с недостающих вопросов. "
            "Перед созданием кейса вы подтвердите итоговую фабулу.",
            reply_markup=quick_intake_runtime._continue_keyboard(draft_id),
        )
        return
    data[_SELECTED] = {"draftId": str(draft_id), "operationId": str(status.operation_id)}
    if status.state == "PENDING" or simplified:
        await bot._reply(
            update, "Разбираю ситуацию. Ничего нажимать не нужно — подождите. "
            "Следующий вопрос или итоговая карточка появится автоматически.",
            reply_markup=_controls(status.operation_id, draft_id, simplified=simplified),
        )
        actor = bot._actor_id(update)
        if actor is not None:
            watch(update, context, selected_key=_SELECTED, operation=status.operation_id,
                  draft_id=draft_id, client=UnderstandingClient(bot._legal_core(context), actor),
                  callback=f"case:draft:{draft_id}" if simplified else bound_callback(
                      "refresh", status.operation_id, draft_id,
                  ))
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


async def controls(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   *, continue_question: bool = False) -> int | None:
    value = await bot._answer_callback(update)
    actor = bot._actor_id(update)
    if value is None or actor is None:
        raise ApplicationHandlerStop
    operation = draft_id = action = None
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
            stop(update, context, _SELECTED)
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
                if continue_question and action == "manual":
                    from telegram_gateway.answer_runtime import _open

                    # This callback runs INSIDE the ConversationHandler. Recheck
                    # the owned snapshot after fencing; never resume cached facts.
                    draft = validated_draft(await core.get_intake_draft(draft_id, actor),
                                            draft_id)
                    return await _open(update, context, draft,
                                       accepted_revision=draft["revision"])
                await bot._reply(
                    update, "Разбор остановлен. Описание и ответы сохранены в черновике."
                    if status.state != "ACCEPTED" else "Подтверждённые сведения сохранены.",
                    reply_markup=quick_intake_runtime._continue_keyboard(draft_id),
                )
        else:
            await _show(update, context, status, draft)
    except (KeyError, TypeError, ValueError, LegalCoreApiError):
        if action in {"menu", "manual"} and draft_id is not None:
            try:
                if await _superseded(bot._legal_core(context), actor, draft_id):
                    stop(update, context, _SELECTED)
                    bot._user_data(context).pop(_SELECTED, None)
                    bot._user_data(context).pop(_EDIT, None)
                    bot._clear_wizard(context)
                    if action == "menu":
                        await bot.menu(update, context)
                    else:
                        await bot._reply(update, "Черновик сохранён. Можно продолжить заполнение.",
                                         reply_markup=quick_intake_runtime._continue_keyboard(
                                             draft_id,
                                         ))
                    raise ApplicationHandlerStop
            except (KeyError, TypeError, ValueError, LegalCoreApiError):
                pass
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
    draft_id = None
    try:
        draft_id, operation = UUID(selected["draftId"]), UUID(selected["operationId"])
        core = bot._legal_core(context)
        status, _draft = await _checked(core, actor, operation, draft_id)
        if status.state in {"PENDING", "PROPOSED"}:
            await UnderstandingClient(core, actor).cancel(operation)
    except (KeyError, TypeError, ValueError, LegalCoreApiError):
        try:
            superseded = draft_id is not None and await _superseded(
                bot._legal_core(context), actor, draft_id,
            )
        except (KeyError, TypeError, ValueError, LegalCoreApiError):
            superseded = False
        if not superseded:
            await bot._answer_callback(update)
            await bot._reply(update, "Не удалось подтвердить остановку разбора. "
                             "Откройте текущий черновик позже; сохранённые ответы не потеряны.")
            raise ApplicationHandlerStop from None
    data.pop(_SELECTED, None)
    stop(update, context, _SELECTED)
    data.pop(_EDIT, None)


def install_understanding(application: bot.TelegramApplication) -> None:
    """Explicit opt-in composition; no global monkeypatch or background PTB tasks."""
    simplified = application.bot_data.get(ENABLED) is True

    async def resume_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        from telegram_gateway.answer_runtime import keep_active_draft

        query = update.callback_query
        try:
            if query is None or not isinstance(query.data, str):
                raise ValueError("selector required")
            _action, _operation, draft_id = parse_bound_callback(query.data)
        except ValueError:
            await bot._answer_callback(update)
            return ConversationHandler.END
        kept = await keep_active_draft(update, context, draft_id)
        if kept is not None:
            return kept
        state = await controls(update, context, continue_question=True)
        return ConversationHandler.END if state is None else state

    async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        value = await bot._answer_callback(update)
        actor = bot._actor_id(update)
        if value is None or actor is None:
            return ConversationHandler.END
        try:
            draft_id = UUID(value.removeprefix("case:draft:"))
            core = bot._legal_core(context)
            draft = validated_draft(await core.get_intake_draft(draft_id, actor), draft_id)
            # Guided drafts have no initial narrative before the EVENT step.
            # Absence is not a failed model operation; present invalid input still
            # crosses the existing fail-closed discovery boundary unchanged.
            status = await UnderstandingClient(core, actor).discover(draft_id) if (
                "eventSummary" in draft["draftData"]
            ) else None
            if status is not None and status.state in {"PENDING", "PROPOSED"}:
                status, draft = await _checked(core, actor, status.operation_id, draft_id)
                if context.bot_data.get(ENABLED) is True and status.state == "PROPOSED":
                    from telegram_gateway.answer_runtime import _open

                    if status.source is None:
                        raise ValueError("current proposal required")
                    saved = await stage(UnderstandingClient(core, actor), status, draft,
                                        answer=False)
                    stop(update, context, _SELECTED)
                    bot._user_data(context).pop(_SELECTED, None)
                    return await _open(update, context, saved,
                                       accepted_revision=status.source.draft_revision + 1)
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
                if simplified and handler.name == "administrator-case-intake-v1":
                    handler.entry_points.insert(0, CallbackQueryHandler(
                        resume_manual, pattern=r"^understanding:m1:",
                    ))
                for entry in handler.entry_points:
                    if entry.callback is bot.resume_intake_draft:
                        entry.callback = resume
    application.add_handler(TypeHandler(Update, fence_navigation), group=-95)
    application.add_handler(CallbackQueryHandler(
        controls, pattern=r"^understanding:(?!m1:)" if simplified else r"^understanding:",
    ), group=-7)
