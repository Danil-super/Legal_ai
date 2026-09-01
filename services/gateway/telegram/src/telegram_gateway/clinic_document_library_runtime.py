# ruff: noqa: RUF001
"""Telegram operator view for tenant clinic document library metadata."""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

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
from telegram_gateway.clinic_document_runtime import build_application_with_clinic_documents

logger = logging.getLogger(__name__)
_MAX_DOCUMENTS = 20
_MAX_VERSIONS = 15
_MAX_MESSAGE = 3900
_HISTORY_CALLBACK_RE = re.compile(
    r"^cliniclib:history:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)


class ClinicDocumentLibraryClient:
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
                "/v1/clinic-document-library",
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
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise LegalCoreApiError(
                502,
                "INVALID_LEGAL_CORE_RESPONSE",
                "Invalid Legal Core response",
            )
        return payload


def _state_icon(state: str) -> str:
    return {
        "APPROVED": "✅",
        "BLOCKED": "⛔",
        "RETIRED": "🗄",
        "PENDING": "🕓",
    }.get(state, "❔")


def _short_sha(value: object) -> str:
    raw = str(value or "")
    return f"{raw[:12]}…" if len(raw) >= 12 else "—"


def _bounded(text: str) -> str:
    if len(text) <= _MAX_MESSAGE:
        return text
    return text[: _MAX_MESSAGE - 40].rstrip() + "\n\n…список сокращён."


def render_library(payload: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup | None]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("items")
    if not raw_items:
        return (
            "📚 База документов клиники пока пуста.\n\n"
            "Добавить документ: /upload_clinic_doc <key> <TYPE> <название>",
            None,
        )

    lines = [
        "📚 Документы клиники",
        "",
        "Показывается только метадата. Содержимое и raw-файлы здесь не выдаются.",
        "",
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for raw_item in raw_items[:_MAX_DOCUMENTS]:
        if not isinstance(raw_item, dict):
            continue
        title = str(raw_item.get("title") or "Без названия")[:120]
        key = str(raw_item.get("documentKey") or "—")[:100]
        doc_type = str(raw_item.get("documentType") or "—")[:80]
        versions = raw_item.get("versions")
        versions_list = versions if isinstance(versions, list) else []
        lines.append(f"• {title}")
        lines.append(f"  {key} · {doc_type}")
        try:
            document_id = UUID(str(raw_item["id"]))
        except (KeyError, TypeError, ValueError):
            document_id = None
        if not versions_list:
            lines.append("  Версий пока нет")
            lines.append("")
            if document_id is not None:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            f"🗂 История {key}"[:60],
                            callback_data=f"cliniclib:history:{document_id}",
                        )
                    ]
                )
            continue
        latest = versions_list[0] if isinstance(versions_list[0], dict) else {}
        state = str(latest.get("reviewState") or "PENDING")
        version_no = latest.get("versionNo")
        filename = str(latest.get("sourceFilename") or "—")[:120]
        lines.append(f"  {_state_icon(state)} v{version_no} · {state} · {filename}")
        lines.append(f"  SHA: {_short_sha(latest.get('rawSha256'))}")
        lines.append("")
        try:
            version_id = UUID(str(latest["id"]))
        except (KeyError, TypeError, ValueError):
            continue
        row = [
            InlineKeyboardButton(
                f"✅ Одобрить {key}"[:60],
                callback_data=f"clinicdoc:approve:{version_id}",
            ),
            InlineKeyboardButton(
                f"⛔ Блок {key}"[:60],
                callback_data=f"clinicdoc:block:{version_id}",
            ),
        ]
        buttons.append(row)
        if document_id is not None:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"🗂 История {key}"[:60],
                        callback_data=f"cliniclib:history:{document_id}",
                    )
                ]
            )

    if len(raw_items) > _MAX_DOCUMENTS:
        lines.append(f"…и ещё {len(raw_items) - _MAX_DOCUMENTS} документов.")
    keyboard = InlineKeyboardMarkup(buttons) if buttons else None
    return _bounded("\n".join(lines)), keyboard


def render_document_history(payload: dict[str, Any], document_id: UUID) -> str:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("items")
    selected: dict[str, Any] | None = None
    for item in raw_items:
        if isinstance(item, dict) and str(item.get("id")) == str(document_id):
            selected = item
            break
    if selected is None:
        raise LookupError("document not found")

    title = str(selected.get("title") or "Без названия")[:160]
    key = str(selected.get("documentKey") or "—")[:100]
    doc_type = str(selected.get("documentType") or "—")[:80]
    versions = selected.get("versions")
    versions_list = versions if isinstance(versions, list) else []
    lines = [
        f"🗂 История: {title}",
        f"{key} · {doc_type}",
        "",
    ]
    if not versions_list:
        lines.append("Версий пока нет.")
        return "\n".join(lines)
    for raw_version in versions_list[:_MAX_VERSIONS]:
        if not isinstance(raw_version, dict):
            continue
        state = str(raw_version.get("reviewState") or "PENDING")
        version_no = raw_version.get("versionNo")
        filename = str(raw_version.get("sourceFilename") or "—")[:120]
        valid_from = str(raw_version.get("validFrom") or "—")
        valid_to = str(raw_version.get("validTo") or "∞")
        lines.append(f"{_state_icon(state)} v{version_no} · {state}")
        lines.append(f"  {filename}")
        lines.append(f"  действует: {valid_from} → {valid_to}")
        lines.append(f"  SHA: {_short_sha(raw_version.get('rawSha256'))}")
        reason = raw_version.get("reviewReasonCode")
        if reason:
            lines.append(f"  review: {str(reason)[:100]}")
        lines.append("")
    if len(versions_list) > _MAX_VERSIONS:
        lines.append(f"…и ещё {len(versions_list) - _MAX_VERSIONS} версий.")
    return _bounded("\n".join(lines))


async def show_clinic_documents(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    user = update.effective_user
    if user is None:
        await gateway_bot._reply(update, "Не удалось определить администратора.")
        return
    client = ClinicDocumentLibraryClient()
    try:
        payload = await client.get_library(user.id)
        text, keyboard = render_library(payload)
    except LegalCoreApiError as exc:
        logger.warning("clinic document library failed: %s", exc.code)
        if exc.status_code == 403:
            error_message = "Доступ администратора не активирован."
        else:
            error_message = "Не удалось получить библиотеку документов клиники."
        await gateway_bot._reply(update, f"⚠️ {error_message}")
        return
    except ValueError:
        await gateway_bot._reply(update, "⚠️ Ответ библиотеки документов некорректен.")
        return
    finally:
        await client.aclose()

    telegram_message = update.effective_message
    if telegram_message is None:
        return
    await telegram_message.reply_text(text, reply_markup=keyboard)


async def show_document_history_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    del context
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not isinstance(query.data, str):
        raise ApplicationHandlerStop
    match = _HISTORY_CALLBACK_RE.fullmatch(query.data)
    if match is None:
        raise ApplicationHandlerStop
    await query.answer()
    document_id = UUID(match.group(1))
    client = ClinicDocumentLibraryClient()
    try:
        payload = await client.get_library(user.id)
        history = render_document_history(payload, document_id)
    except LegalCoreApiError as exc:
        logger.warning("clinic document history failed: %s", exc.code)
        await gateway_bot._reply(update, "⚠️ Не удалось получить историю документа.")
        raise ApplicationHandlerStop from exc
    except (LookupError, ValueError) as exc:
        await gateway_bot._reply(update, "⚠️ Документ или его история недоступны.")
        raise ApplicationHandlerStop from exc
    finally:
        await client.aclose()
    try:
        await query.edit_message_text(text=history)
    except Exception:  # pragma: no cover - Telegram edit fallback remains operator-visible.
        logger.exception("clinic document history message edit failed")
        await gateway_bot._reply(update, history)
    raise ApplicationHandlerStop


def build_application_with_clinic_document_library(token: str) -> gateway_bot.TelegramApplication:
    application = build_application_with_clinic_documents(token)
    application.add_handler(CommandHandler("clinic_docs", show_clinic_documents), group=-2)
    application.add_handler(
        CallbackQueryHandler(
            show_document_history_callback,
            pattern=(
                r"^cliniclib:history:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}$"
            ),
        ),
        group=-2,
    )
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    application = build_application_with_clinic_document_library(gateway_bot.load_token())
    application.run_polling(
        allowed_updates=gateway_bot.ALLOWED_UPDATES,
        bootstrap_retries=3,
        drop_pending_updates=False,
    )
