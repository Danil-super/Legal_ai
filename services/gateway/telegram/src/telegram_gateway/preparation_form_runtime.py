"""Explicit composition for one shared pre-report confirmation in both V2 entries."""

import re

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler

from telegram_gateway import bot


async def confirm_preparation_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await bot.confirm_v2_summary(update, context, preparation=True)


def install_preparation_form(application: bot.TelegramApplication) -> None:
    """Opt-in during application construction; no global callback monkey-patching."""
    for handlers in application.handlers.values():
        for handler in handlers:
            if isinstance(handler, ConversationHandler) and handler.name == (
                "administrator-case-intake-v1"
            ):
                for step in handler.states[bot.WizardState.SUMMARY]:
                    if (isinstance(step, CallbackQueryHandler)
                            and isinstance(step.pattern, re.Pattern)
                            and step.pattern.pattern == r"^case:v2:summary:confirm$"):
                        step.callback = bot._persisted(
                            bot.confirm_case if application.bot_data.get(
                                "intake_simplified_dialogue"
                            ) is True else confirm_preparation_summary,
                        )
                        return
    raise RuntimeError("shared intake summary handler is missing")
