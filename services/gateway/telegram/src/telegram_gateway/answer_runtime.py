# ruff: noqa: RUF001
"""App-local subsequent-answer controls; no model waits or background user-data mutation."""

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from legal_core.intake_answer_contracts import derive_answer_question
from legal_core.intake_answer_wire import AnswerStatusResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    TypeHandler,
)

from telegram_gateway import bot
from telegram_gateway.answer_client import AnswerClient
from telegram_gateway.answer_intent import bound_callback, parse_bound_callback
from telegram_gateway.answer_review import review_blocks
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.escalation_workspace import _chunks
from telegram_gateway.guided_case_intake_v2 import next_missing_state
from telegram_gateway.understanding_client import validated_draft

_SELECTED = "answer_interpretation"
_MANUAL = "answer_manual_once"
_STATES = frozenset({"INCOMING", "SITUATION", "SERVICES", "EVENT", "EVENT_DATE", "CHRONOLOGY",
                     "CLINIC_ACTIONS", "HEALTH", "SAFETY", "ANALYSIS_CLARIFICATION"})
Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, int]]


def _controls(operation: UUID, draft_id: UUID, *, proposed: bool = False) -> InlineKeyboardMarkup:
    actions = ([("✅ Всё верно — сохранить ответ", "accept")] if proposed else []) + [
        ("Проверить результат", "refresh"), ("Ответить точнее", "edit"),
        ("Заполнить вручную", "manual"), ("Отменить разбор", "cancel"),
        ("← Главное меню", "menu"),
    ]
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        label, callback_data=bound_callback(action, operation, draft_id),
    )] for label, action in actions])


async def _draft(core: Any, actor: int, draft_id: UUID) -> dict[str, Any]:
    return validated_draft(await core.get_intake_draft(draft_id, actor), draft_id)


async def _checked(core: Any, actor: int, operation: UUID,
                   draft_id: UUID) -> tuple[AnswerStatusResponse, dict[str, Any]]:
    client = AnswerClient(core, actor)
    draft, status = await _draft(core, actor, draft_id), await client.get(operation)
    binding = status.source or status.acceptance
    if binding is not None:
        if binding.draft_id != draft_id:
            raise ValueError("operation belongs to another draft")
        if status.acceptance is not None and draft["revision"] < (
            status.acceptance.accepted_revision
        ):
            draft = await _draft(core, actor, draft_id)
            if draft["revision"] < status.acceptance.accepted_revision:
                raise ValueError("receipt newer than saved draft")
    else:
        discovered = await client.discover(draft_id)
        if discovered is None or discovered.operation_id != operation:
            # Cancelled operations are intentionally absent from discovery. A bound
            # historical GET alone cannot prove their draft, so fail closed here.
            raise ValueError("operation no longer belongs to current question")
    if status.state in {"PROPOSED", "UNRESOLVED"}:
        await client.proposal_draft(status, draft)
    return status, draft


async def _cancel(client: AnswerClient, operation: UUID) -> None:
    try:
        await client.cancel(operation)
        return
    except LegalCoreApiError:
        pass
    # A lost DELETE response is not cancellation proof; read its durable result.
    if (await client.get(operation)).state != "MANUAL_CONTINUATION":
        raise ValueError("cancellation outcome unknown")


async def _open(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: dict[str, Any],
                *, accepted_revision: int | None = None) -> int:
    actor = bot._actor_id(update)
    if actor is None:
        raise ValueError("actor missing")
    draft_id = UUID(draft["id"])
    if accepted_revision is not None and draft["revision"] == accepted_revision:
        # Only a just-accepted snapshot advances automatically. Old receipt replay
        # never changes a later explicit back/edit navigation checkpoint.
        following = next_missing_state(draft["draftData"])
        if following != draft["wizardState"]:
            draft = validated_draft(await bot._legal_core(context).save_intake_draft(
                draft_id, actor, expected_revision=draft["revision"], wizard_state=following,
                draft_data=draft["draftData"],
            ), draft_id)
    data = bot._saved_draft_selection(draft_id, draft)
    bot._user_data(context)[bot.WIZARD_DATA_KEY] = data
    state = bot.WizardState[data[bot.DRAFT_STATE_KEY]]
    await bot._prompt_v2_draft(update, state, data, preparation=True)
    return state


async def _show(update: Update, context: ContextTypes.DEFAULT_TYPE, status: AnswerStatusResponse,
                draft: dict[str, Any]) -> int:
    draft_id = UUID(draft["id"])
    data = bot._user_data(context)
    if status.state == "ACCEPTED":
        if status.acceptance is None:
            raise ValueError("acceptance receipt missing")
        data.pop(_SELECTED, None)
        await bot._reply(update, "Ответ сохранён. Продолжим с недостающих сведений.")
        return await _open(update, context, draft,
                           accepted_revision=status.acceptance.accepted_revision)
    data[_SELECTED] = {"draftId": str(draft_id), "operationId": str(status.operation_id)}
    if status.state == "MANUAL_CONTINUATION":
        data.pop(_SELECTED, None)
        return await _open(update, context, draft)
    if status.state == "PENDING":
        pages = ["Разбираем ответ на текущий вопрос. Факт ещё не подтверждён. "
                 "Можно проверить результат или продолжить вручную, не дожидаясь разбора."]
    else:
        pages = list(_chunks(review_blocks(status, draft["draftData"])))
    for index, text in enumerate(pages):
        await bot._reply(update, text, reply_markup=_controls(
            status.operation_id, draft_id, proposed=status.state == "PROPOSED",
        ) if index == len(pages) - 1 else None)
    return bot.WizardState[draft["wizardState"]]


async def controls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    callback, actor = await bot._answer_callback(update), bot._actor_id(update)
    operation = draft_id = None
    try:
        if callback is None or actor is None:
            raise ValueError("actor or selector missing")
        action, operation, draft_id = parse_bound_callback(callback)
        core, client = bot._legal_core(context), AnswerClient(bot._legal_core(context), actor)
        status, draft = await _checked(core, actor, operation, draft_id)
        if action == "accept":
            if status.state == "PROPOSED":
                draft = await client.accept(status)
                status = await client.get(operation)
            if status.state != "ACCEPTED":
                raise ValueError("no confirmable answer")
            return await _show(update, context, status, draft)
        if action == "refresh":
            return await _show(update, context, status, draft)
        if status.state != "ACCEPTED":
            await _cancel(client, operation)
        elif action not in {"menu", "manual"}:
            raise ValueError("confirmed answer cannot be cancelled")
        data = bot._user_data(context)
        data.pop(_SELECTED, None)
        data.pop(_MANUAL, None)
        if action == "menu":
            bot._clear_wizard(context)
            await bot.menu(update, context)
            return ConversationHandler.END
        if action == "manual":
            data[_MANUAL] = {"draftId": str(draft_id), "revision": draft["revision"]}
        await bot._reply(update, "Разбор остановлен; сохранённые ответы не изменены. "
                         "Выберите вариант в анкете." if action == "manual" else
                         "Разбор остановлен. Напишите ответ на текущий вопрос заново.")
        return await _open(update, context, draft)
    except (ValueError, KeyError, TypeError, LegalCoreApiError):
        await bot._reply(update, "Не удалось подтвердить результат действия. "
                         "Ничего не считаем сохранённым или отменённым до проверки.",
                         reply_markup=_controls(operation, draft_id)
                         if operation is not None and draft_id is not None else None)
        return ConversationHandler.END


async def route_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Short authoritative reads; no poll loop and no waiting for a model result."""
    data = bot._user_data(context)
    callback = update.callback_query.data if update.callback_query is not None else None
    if isinstance(callback, str) and callback.startswith(
        ("answer:", "case:draft:", "analysis:pdf:", "esc:pdf:"),
    ):
        return
    wizard = data.get(bot.WIZARD_DATA_KEY)
    selected = data.get(_SELECTED)
    if not isinstance(selected, dict) and not (
        isinstance(wizard, dict) and wizard.get("intakeVersion") == 2
    ):
        return
    factual = isinstance(wizard, dict) and wizard.get(bot.DRAFT_STATE_KEY) in _STATES
    text = bot._message_text(update) if update.callback_query is None else ""
    interpret = bool(factual and text and not text.startswith("/"))
    if not interpret and not isinstance(callback, str) and not text.startswith("/"):
        return
    identity = selected if isinstance(selected, dict) else wizard
    if not isinstance(identity, dict):
        return
    actor = bot._actor_id(update)
    try:
        if actor is None:
            raise ValueError("actor missing")
        draft_id = UUID(identity.get("draftId", identity.get(bot.DRAFT_ID_KEY)))
        core, client = bot._legal_core(context), AnswerClient(bot._legal_core(context), actor)
        status = await client.discover(draft_id)
        if not interpret:
            if status is not None:
                await _cancel(client, status.operation_id)
            data.pop(_SELECTED, None)
            data.pop(_MANUAL, None)
            return
        remote = await _draft(core, actor, draft_id)
        if not isinstance(wizard, dict):
            raise ValueError("current question missing")
        if status is not None:
            await bot._reply(update, "Предыдущий ответ ещё ожидает проверки. "
                             "Новый ответ пока не сохранён. Чтобы заменить его, "
                             "нажмите «Ответить точнее».")
            await _show(update, context, status, remote)
            raise ApplicationHandlerStop
        if remote["revision"] != wizard.get(bot.DRAFT_REVISION_KEY):
            await _open(update, context, remote)
            raise ApplicationHandlerStop
        local_state = wizard[bot.DRAFT_STATE_KEY]
        if remote["wizardState"] != local_state:
            if next_missing_state(remote["draftData"]) != local_state:
                raise ValueError("stale question")
            remote = validated_draft(await core.save_intake_draft(
                draft_id, actor, expected_revision=remote["revision"], wizard_state=local_state,
                draft_data=remote["draftData"],
            ), draft_id)
            wizard.update(bot._saved_draft_selection(draft_id, remote))
        manual = data.get(_MANUAL)
        if manual == {"draftId": str(draft_id), "revision": remote["revision"]}:
            if local_state not in {
                "SERVICES", "EVENT", "EVENT_DATE", "SAFETY", "ANALYSIS_CLARIFICATION",
            }:
                await bot._reply(update, "В ручном режиме выберите вариант кнопкой под вопросом.")
                raise ApplicationHandlerStop
            return  # ordinary button/date/text parser; no model source or automatic UNKNOWN
        data.pop(_MANUAL, None)
        if derive_answer_question(remote["draftData"], remote["wizardState"]) is None:
            raise ValueError("no current factual question")
        status = await client.start(draft_id, remote["revision"], text)
        await _show(update, context, status, remote)
    except (ValueError, KeyError, TypeError, LegalCoreApiError):
        await bot._reply(update, "Ответ пока не подтверждён. Проверьте обезличивание и "
                         "откройте текущий черновик; сохранённые ответы не потеряны.")
    raise ApplicationHandlerStop


def _resume_handler(
    original: Callable[[Update, Any], Coroutine[Any, Any, object]],
) -> Handler:
    async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        actor, query = bot._actor_id(update), update.callback_query
        try:
            if actor is None or query is None or not isinstance(query.data, str):
                raise ValueError("actor missing")
            draft_id = UUID(query.data.removeprefix("case:draft:"))
            client = AnswerClient(bot._legal_core(context), actor)
            status = await client.discover(draft_id)
            if status is not None:
                await bot._answer_callback(update)
                status, draft = await _checked(
                    bot._legal_core(context), actor, status.operation_id, draft_id,
                )
                bot._user_data(context)[bot.WIZARD_DATA_KEY] = (
                    bot._saved_draft_selection(draft_id, draft)
                )
                return await _show(update, context, status, draft)
        except (ValueError, KeyError, TypeError, LegalCoreApiError):
            await bot._reply(update, "Черновик или разбор сейчас недоступен. "
                             "Обновите список через /menu.")
            return ConversationHandler.END
        state = await original(update, context)
        if not isinstance(state, int):
            raise ValueError("intake resume returned no conversation state")
        return state

    return resume


def install_answer_interpretation(application: bot.TelegramApplication) -> None:
    """Explicit opt-in factory hook, keeping the default production composition unchanged."""
    for handlers in application.handlers.values():
        for conversation in handlers:
            if isinstance(conversation, ConversationHandler) and conversation.name == (
                "administrator-case-intake-v1"
            ):
                for entry in conversation.entry_points:
                    pattern = getattr(entry, "pattern", None)
                    if pattern is not None and pattern.pattern.startswith("^case:draft:"):
                        entry.callback = _resume_handler(entry.callback)
                conversation.entry_points.insert(
                    0, CallbackQueryHandler(controls, pattern=r"^answer:"),
                )
                application.add_handler(TypeHandler(Update, route_input), group=-94)
                return
    raise RuntimeError("durable clinic intake conversation is missing")
