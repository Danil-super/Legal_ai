"""Upload-purpose flag and ephemeral, delivered-prompt consent marker."""

import os

from telegram import Update

OFFER_KEY = "_v2_case_material_analysis_offer"


def enabled() -> bool:
    return os.getenv("CASE_MATERIAL_CONTEXT_ENABLED", "0").strip().casefold() in {"1", "true"}


def informed_upload(
    update: Update, actor: int, *, draft_id: object, prompt_id: object, offer: object,
) -> bool:
    """A queued old message cannot consent to a purpose notice delivered later."""
    message = getattr(update, "message", None)
    incoming_id = getattr(message, "message_id", None)
    sender, chat = getattr(message, "from_user", None), getattr(message, "chat", None)
    return (
        enabled() and offer == (str(draft_id), prompt_id)
        and type(prompt_id) is int and prompt_id > 0
        and type(incoming_id) is int and incoming_id > prompt_id
        and getattr(sender, "id", None) == actor and getattr(sender, "is_bot", True) is False
        and getattr(chat, "type", None) == "private" and getattr(chat, "id", None) == actor
    )
