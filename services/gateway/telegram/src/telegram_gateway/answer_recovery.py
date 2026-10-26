"""Resume owned current facts after an obsolete operation, never infer a lost write."""

from uuid import UUID

from legal_core.intake_answer_contracts import derive_answer_question
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from telegram_gateway import bot
from telegram_gateway.answer_client import AnswerClient
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.intake_cleanup import finish_waiting_prompt
from telegram_gateway.intake_delivery import stop
from telegram_gateway.understanding_client import validated_draft


async def recover_current(update: Update, context: ContextTypes.DEFAULT_TYPE, *,
                          action: str, operation: UUID, draft_id: UUID) -> int | None:
    """Discovery must positively establish no live work for this owned question."""
    from telegram_gateway.answer_runtime import _MANUAL, _SELECTED, _open, keep_active_draft

    actor = bot._actor_id(update)
    if actor is None or action not in {"refresh", "edit", "manual", "menu"}:
        return None
    data = bot._user_data(context)
    expected = {"draftId": str(draft_id), "operationId": str(operation)}
    selected = data.get(_SELECTED)
    if isinstance(selected, dict) and selected != expected:
        return None
    kept = await keep_active_draft(update, context, draft_id)
    if kept is not None:
        return kept
    core = bot._legal_core(context)
    try:
        draft = validated_draft(await core.get_intake_draft(draft_id, actor), draft_id)
        if await AnswerClient(core, actor).discover(draft_id) is not None:
            return None
    except (ValueError, KeyError, TypeError, LegalCoreApiError):
        return None
    stop(update, context, _SELECTED)
    data.pop(_SELECTED, None)
    data.pop(_MANUAL, None)
    if action == "menu":
        bot._clear_wizard(context)
        await bot.menu(update, context)
        return ConversationHandler.END
    question = derive_answer_question(draft["draftData"], draft["wizardState"])
    if action == "manual" or (question is not None and question.screening_field == "amount"):
        data[_MANUAL] = {"draftId": str(draft_id), "revision": draft["revision"]}
    # A completed current question can move to the next missing one. This is
    # navigation over re-read facts, not proof that the old operation was saved.
    following = await _open(update, context, draft,
                            accepted_revision=draft["revision"] if question is None else None)
    await finish_waiting_prompt(update, context, operation,
                                bot._wizard_data(context).get("_v2_prompt_message_id"))
    return following
