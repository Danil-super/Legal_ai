"""Exact, ephemeral step IDs: no history sweep, no text storage, no restart guessing."""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

KEY = "intake_transient_step"
_PROTECTED = frozenset({"SUMMARY", "CONFIRM", "V2_CONFIRM"})
_SHORT_TEXT = frozenset({"INCOMING", "SITUATION", "SERVICES", "EVENT_DATE", "CHRONOLOGY",
                         "CLINIC_ACTIONS", "HEALTH", "SAFETY", "ANALYSIS_CLARIFICATION"})
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Step:
    actor: int
    chat: int
    draft: str
    revision: int
    messages: tuple[int, ...]
    waits: tuple[int, ...] = ()
    saved_revision: int | None = None
    saved_sha256: str | None = None


def capture(update: Update, draft: str, revision: object, state: str,
            prompt: object = None, *, answer: bool = True) -> Step | None:
    user, chat = update.effective_user, update.effective_chat
    if (user is None or chat is None or chat.type != "private"
            or type(revision) is not int or revision < 1):
        return None
    ids = [prompt] if type(prompt) is int and prompt > 0 and state not in _PROTECTED else []
    message = update.message  # Never infer a user answer from a synthetic callback anchor.
    if (answer and state in _SHORT_TEXT and message is not None and message.text
            and not message.document and not message.photo and len(message.text) <= 500
            and not message.text.startswith("/") and message.from_user is not None
            and message.from_user.id == user.id and not message.from_user.is_bot):
        ids.append(message.message_id)
    return Step(user.id, chat.id, str(UUID(draft)), revision, tuple(dict.fromkeys(ids)))


def remember_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, operation: UUID,
                    draft: dict[str, Any], wizard: dict[str, Any]) -> None:
    step = capture(update, draft["id"], draft["revision"], draft["wizardState"],
                   wizard.get("_v2_prompt_message_id"))
    if step is not None and context.user_data is not None:
        context.user_data[KEY] = (operation, step)


def remember_wait(update: Update, context: ContextTypes.DEFAULT_TYPE, operation: UUID,
                  draft: dict[str, Any], sent: object) -> None:
    if context.user_data is None:
        return
    step = capture(update, draft["id"], draft["revision"], draft["wizardState"], answer=False)
    message_id = getattr(sent, "message_id", None)
    chat_id = getattr(sent, "chat_id", None)
    if step is None or type(message_id) is not int or message_id < 1 or chat_id != step.chat:
        return
    previous = context.user_data.get(KEY)
    if isinstance(previous, tuple) and len(previous) == 2 and previous[0] == operation:
        old = previous[1]
        if isinstance(old, Step) and (old.actor, old.chat, old.draft, old.revision) == (
            step.actor, step.chat, step.draft, step.revision,
        ):
            step = old
    # A bounded number of explicitly sent waits, never a range of message IDs.
    ids = tuple(dict.fromkeys((*step.messages, message_id)))
    waits = tuple(dict.fromkeys((*step.waits, message_id)))
    context.user_data[KEY] = (operation, replace(step, messages=ids[-8:], waits=waits[-8:]))


async def delete_saved(update: Update, context: ContextTypes.DEFAULT_TYPE, step: Step | None,
                       saved: dict[str, Any], current_prompt: object) -> None:
    user, chat = update.effective_user, update.effective_chat
    revision = saved.get("revision")
    if (step is None or user is None or chat is None or chat.type != "private"
            or (user.id, chat.id) != (step.actor, step.chat) or saved.get("id") != step.draft
            or type(revision) is not int or revision <= step.revision
            or type(current_prompt) is not int or current_prompt < 1):
        return
    await _delete_ids(update, context, step, current_prompt)


async def _delete_ids(update: Update, context: ContextTypes.DEFAULT_TYPE, step: Step,
                      current_prompt: object) -> None:
    user, chat = update.effective_user, update.effective_chat
    if (user is None or chat is None or chat.type != "private"
            or (user.id, chat.id) != (step.actor, step.chat)
            or type(current_prompt) is not int or current_prompt < 1):
        return
    try:
        async with asyncio.timeout(2):
            for message_id in step.messages:
                if message_id == current_prompt:
                    continue
                try:
                    await context.bot.delete_message(chat_id=step.chat, message_id=message_id)
                except TelegramError:
                    logger.info("intake transient message deletion unavailable")
    except TimeoutError:
        logger.info("intake transient message deletion timed out")


async def finish_waiting(update: Update, context: ContextTypes.DEFAULT_TYPE, operation: UUID,
                         sent: object) -> None:
    """An unresolved answer stays; only its obsolete wait can disappear after correction UI."""
    if context.user_data is None:
        return
    previous = context.user_data.get(KEY)
    if not (isinstance(previous, tuple) and len(previous) == 2
            and previous[0] == operation and isinstance(previous[1], Step)):
        return
    step = previous[1]
    if getattr(sent, "chat_id", None) != step.chat:
        return
    await _delete_ids(update, context, replace(step, messages=step.waits),
                      getattr(sent, "message_id", None))


async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE, operation: UUID,
                 saved: dict[str, Any], wizard: dict[str, Any]) -> None:
    if context.user_data is None:
        return
    previous = context.user_data.get(KEY)
    if not (isinstance(previous, tuple) and len(previous) == 2
            and previous[0] == operation and isinstance(previous[1], Step)):
        return
    context.user_data.pop(KEY, None)  # Freeze IDs before awaiting Telegram or later navigation.
    await delete_saved(update, context, previous[1], saved, wizard.get("_v2_prompt_message_id"))


def remember_saved(context: ContextTypes.DEFAULT_TYPE, operation: UUID,
                   saved: dict[str, Any]) -> None:
    """Retain staging proof before next-prompt I/O, not an inference from revision movement."""
    if context.user_data is None:
        return
    previous = context.user_data.get(KEY)
    if not (isinstance(previous, tuple) and len(previous) == 2
            and previous[0] == operation and isinstance(previous[1], Step)):
        return
    step, revision = previous[1], saved.get("revision")
    if saved.get("id") != step.draft or type(revision) is not int or revision <= step.revision:
        return
    fingerprint = hashlib.sha256(json.dumps(
        saved["draftData"], sort_keys=True, ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()
    context.user_data[KEY] = (operation, replace(
        step, saved_revision=revision, saved_sha256=fingerprint,
    ))


async def recover_saved(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        saved: dict[str, Any], wizard: dict[str, Any]) -> None:
    """Called only after an authorized resume has actually delivered its current prompt."""
    if context.user_data is None:
        return
    previous = context.user_data.get(KEY)
    if not (isinstance(previous, tuple) and len(previous) == 2 and isinstance(previous[1], Step)):
        return
    operation, step = previous
    revision = saved.get("revision")
    if (step.saved_revision is None or step.saved_sha256 is None
            or saved.get("id") != step.draft or type(revision) is not int
            or revision < step.saved_revision):
        return
    fingerprint = hashlib.sha256(json.dumps(
        saved["draftData"], sort_keys=True, ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()
    if fingerprint == step.saved_sha256:
        await finish(update, context, operation, saved, wizard)
