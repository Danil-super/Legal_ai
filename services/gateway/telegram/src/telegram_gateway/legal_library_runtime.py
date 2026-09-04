# ruff: noqa: RUF001
"""Read-only Telegram workspace for the approved legal corpus.

This module deliberately exposes source metadata and official links only. A clinic lawyer can
inspect what Legal Core is allowed to retrieve for a report, but cannot upload or approve a law
through a Telegram chat.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx2
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.quick_intake_runtime import build_application_with_quick_intake

logger = logging.getLogger(__name__)
LEGAL_LIBRARY_CALLBACK = "legalbase:open"
_MAX_DOCUMENTS = 20
_MAX_MESSAGE = 3_900


class LegalLibraryClient:
    """Small, defensive client for the lawyer-only Legal Core view."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._http = client or httpx2.AsyncClient(
            base_url=base_url or gateway_bot.load_legal_core_url(),
            timeout=20.0,
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def get_library(self, telegram_user_id: int) -> dict[str, Any]:
        try:
            response = await self._http.get(
                "/v1/legal/library",
                headers={"X-Telegram-User-Id": str(telegram_user_id)},
            )
        except httpx2.HTTPError as exc:
            raise LegalCoreApiError(
                503,
                "LEGAL_CORE_UNAVAILABLE",
                "Legal Core unavailable",
            ) from exc
        if 300 <= response.status_code < 400:
            raise LegalCoreApiError(
                502,
                "LEGAL_CORE_REDIRECT_REJECTED",
                "Legal Core redirect rejected",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise LegalCoreApiError(
                502,
                "INVALID_LEGAL_CORE_RESPONSE",
                "Invalid Legal Core response",
            ) from exc
        if response.status_code >= 400:
            code = "LEGAL_CORE_ERROR"
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                raw_code = payload["error"].get("code")
                if isinstance(raw_code, str) and raw_code:
                    code = raw_code
            raise LegalCoreApiError(
                response.status_code,
                code,
                "Legal Core rejected library request",
            )
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("asOfDate"), str)
            or not isinstance(payload.get("items"), list)
        ):
            raise LegalCoreApiError(
                502,
                "INVALID_LEGAL_CORE_RESPONSE",
                "Invalid Legal Core response",
            )
        return payload


def _bounded(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return "—"
    normalized = " ".join(value.split())
    return normalized[:limit] if normalized else "—"


def _short_sha(value: object) -> str:
    raw = value if isinstance(value, str) else ""
    return f"{raw[:12]}…" if len(raw) == 64 else "—"


def _official_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2_000:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return value


def _applicability(effective_from: object, effective_to: object) -> str:
    start = _bounded(effective_from, limit=10)
    end = _bounded(effective_to, limit=10)
    if end == "—":
        return f"Действует: {start}"
    return f"Действует: {start} — {end}"


def _bounded_message(text: str) -> str:
    if len(text) <= _MAX_MESSAGE:
        return text
    return text[: _MAX_MESSAGE - 36].rstrip() + "\n\n…список сокращён."


def render_legal_library(payload: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    """Render only auditable public-source metadata, never legal text or report contents."""

    raw_items = payload.get("items")
    as_of_date = _bounded(payload.get("asOfDate"), limit=10)
    if not isinstance(raw_items, list):
        raise ValueError("legal library items")
    if not raw_items:
        return (
            "📜 НОРМАТИВНАЯ БАЗА\n\n"
            f"На {as_of_date} в Legal Core нет одобренных источников, "
            "применимых к отчёту.\n\n"
            "Юридические выводы и черновики ответов должны "
            "оставаться заблокированными, пока "
            "платформенный legal editor не проверит "
            "и не одобрит официальные документы.",
            gateway_bot.back_keyboard(),
        )

    lines = [
        "📜 НОРМАТИВНАЯ БАЗА",
        "",
        "Одобренные документы, из которых Legal Core "
        f"может выбирать нормы на {as_of_date}.",
        "В конкретный отчёт попадут только применимые "
        "фрагменты; их перечень остаётся в самом отчёте.",
        "",
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for raw_item in raw_items[:_MAX_DOCUMENTS]:
        if not isinstance(raw_item, dict):
            continue
        title = _bounded(raw_item.get("documentTitle"), limit=180)
        issuer = _bounded(raw_item.get("issuer"), limit=180)
        official_number = _bounded(raw_item.get("officialNumber"), limit=80)
        fragment_count = raw_item.get("fragmentCount")
        count_text = (
            str(fragment_count)
            if isinstance(fragment_count, int) and fragment_count > 0
            else "—"
        )
        lines.extend(
            [
                f"• {title}",
                f"  {issuer}" + (f" · № {official_number}" if official_number != "—" else ""),
                f"  {_applicability(raw_item.get('effectiveFrom'), raw_item.get('effectiveTo'))}",
                "  Фрагментов: "
                f"{count_text} · SHA-256: {_short_sha(raw_item.get('rawSha256'))}",
                "",
            ]
        )
        source_url = _official_url(raw_item.get("sourceUrl"))
        if source_url is not None:
            button_label = f"📄 {official_number}"
            if official_number == "—":
                button_label = f"📄 Источник {len(buttons) + 1}"
            buttons.append([InlineKeyboardButton(button_label[:64], url=source_url)])

    if len(raw_items) > _MAX_DOCUMENTS:
        lines.append(f"…и ещё {len(raw_items) - _MAX_DOCUMENTS} документов.")
    buttons.extend(gateway_bot.back_keyboard().inline_keyboard)
    return _bounded_message("\n".join(lines)), InlineKeyboardMarkup(buttons)


async def show_legal_library(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None:
        await gateway_bot._reply(
            update,
            "Не удалось определить пользователя.",
        )
        return
    client = LegalLibraryClient()
    try:
        text, keyboard = render_legal_library(await client.get_library(actor_id))
    except LegalCoreApiError as exc:
        logger.warning("legal library load failed: %s", exc.code)
        if exc.code == "LEGAL_LIBRARY_NOT_ALLOWED":
            message = (
                "🔒 Нормативная база доступна "
                "юристу и владельцу клиники."
            )
        elif exc.code == "SUBSCRIPTION_INACTIVE":
            message = "🔒 Доступ клиники не активирован."
        else:
            message = (
                "⚠️ Не удалось загрузить "
                "нормативную базу. Попробуйте позже."
            )
        await gateway_bot._reply(update, message)
        return
    except ValueError:
        await gateway_bot._reply(
            update,
            "⚠️ Ответ нормативной базы некорректен.",
        )
        return
    finally:
        await client.aclose()

    message = update.effective_message
    if message is not None:
        await message.reply_text(text, reply_markup=keyboard)


async def legal_library_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if await gateway_bot._answer_callback(update) != LEGAL_LIBRARY_CALLBACK:
        raise ApplicationHandlerStop
    await show_legal_library(update, context)
    raise ApplicationHandlerStop


def build_application_with_legal_library(token: str) -> gateway_bot.TelegramApplication:
    """Compose the complete production gateway with the lawyer-only source view."""

    application = build_application_with_quick_intake(token)
    application.add_handler(CommandHandler("legal_base", show_legal_library), group=-4)
    application.add_handler(
        CallbackQueryHandler(legal_library_callback, pattern=r"^legalbase:open$"),
        group=-4,
    )
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    application = build_application_with_legal_library(gateway_bot.load_token())
    application.run_polling(
        allowed_updates=gateway_bot.ALLOWED_UPDATES,
        bootstrap_retries=3,
        drop_pending_updates=False,
    )
