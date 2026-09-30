# ruff: noqa: RUF001
"""Approved legal library plus a bounded, human-only platform editor workspace."""

from __future__ import annotations

import hashlib
import logging
import re
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx2
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.editor_delivery import EditorFileDeliveryQueue
from telegram_gateway.quick_intake_runtime import build_application_with_quick_intake
from telegram_gateway.reference_evaluation_runtime import install_reference_evaluations
from telegram_gateway.ui import back_keyboard

logger = logging.getLogger(__name__)
LEGAL_LIBRARY_CALLBACK = "legalbase:open"
_MAX_DOCUMENTS = 20
_MAX_MESSAGE = 3_900
_EDITOR_PENDING_KEY = "legal_editor_pending"
_EDITOR_ARTIFACT_MAX_BYTES = 50_000_000
_EDITOR_DELIVERY_KEY = "legal_editor_file_deliveries"
_EDITOR_GROUP_PENDING_KEY = "legal_editor_group_pending"
_EDITOR_REFERENCE_PENDING_KEY = "legal_editor_reference_pending"
_VERIFIED_COPY_SOURCE_LABELS = {
    "www.consultant.ru": "КонсультантПлюс",
    "internet.garant.ru": "Гарант",
}
_EDITOR_CALLBACK_RE = re.compile(
    r"^editor:(?:open|page:(?:[1-9]|[1-9][0-9]|100)|"
    r"materials:(?:[1-9]|[1-9][0-9]|100)|"
    r"group:(?:clinical|labour|courts|privacy|licensing|healthcare|general|other):"
    r"(?:[1-9]|[1-9][0-9]|100)|"
    r"detail:[0-9a-f-]{36}:(?:[1-9]|[1-9][0-9]|100)|"
    r"artifact:[0-9a-f-]{36}|"
    r"material:[0-9a-f-]{36}|"
    r"preparation:[0-9a-f-]{36}|"
    r"batch:(?:clinical|labour|courts|privacy|licensing|healthcare|general)|"
    r"refbatch:(?:clinical|labour|courts|privacy|licensing|healthcare|general)|"
    r"batchpage:[0-9a-f-]{36}:(?:[1-9]|[1-9][0-9]|100)|"
    r"batchconfirm:[0-9a-f-]{36}|"
    r"refbatchpage:[0-9a-f-]{36}:(?:[1-9]|[1-9][0-9]|100)|"
    r"refconfirm:[0-9a-f-]{36}|"
    r"excerpts:[0-9a-f-]{36}|"
    r"fragments:[0-9a-f-]{36}:(?:[1-9]|[1-9][0-9]|100)|"
    r"attest:[0-9a-f-]{36}:(?:source|artifact|dates|fragments)|"
    r"confirm:[0-9a-f-]{36})$"
)


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
        self._editor_gateway_key = gateway_bot.load_legal_editor_gateway_key()

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

    def _editor_headers(
        self, telegram_user_id: int, *, idempotency_key: UUID | None = None
    ) -> dict[str, str]:
        if self._editor_gateway_key is None:
            raise LegalCoreApiError(
                503,
                "LEGAL_EDITOR_WORKSPACE_UNAVAILABLE",
                "Legal editor workspace unavailable",
            )
        headers = {
            "X-Telegram-User-Id": str(telegram_user_id),
            "X-Legal-Editor-Gateway-Key": self._editor_gateway_key,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = str(idempotency_key)
        return headers

    async def _editor_json(
        self,
        method: str,
        path: str,
        telegram_user_id: int,
        *,
        payload: dict[str, Any] | None = None,
        idempotency_key: UUID | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method,
                path,
                headers=self._editor_headers(telegram_user_id, idempotency_key=idempotency_key),
                json=payload,
            )
        except httpx2.HTTPError as exc:
            raise LegalCoreApiError(
                503, "LEGAL_CORE_UNAVAILABLE", "Legal Core unavailable"
            ) from exc
        if 300 <= response.status_code < 400:
            raise LegalCoreApiError(502, "LEGAL_CORE_REDIRECT_REJECTED", "Redirect rejected")
        try:
            body = response.json()
        except ValueError as exc:
            raise LegalCoreApiError(502, "INVALID_LEGAL_CORE_RESPONSE", "Invalid response") from exc
        if response.status_code >= 400:
            code = "LEGAL_CORE_ERROR"
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                raw_code = body["error"].get("code")
                if isinstance(raw_code, str) and raw_code:
                    code = raw_code
            raise LegalCoreApiError(response.status_code, code, "Legal Core rejected request")
        if not isinstance(body, dict):
            raise LegalCoreApiError(502, "INVALID_LEGAL_CORE_RESPONSE", "Invalid response")
        return body

    async def get_review_queue(self, telegram_user_id: int, *, page: int = 1) -> dict[str, Any]:
        payload = await self._editor_json(
            "GET", f"/v1/legal/review-queue?page={page}", telegram_user_id
        )
        if (
            not isinstance(payload.get("items"), list)
            or payload.get("page") != page
            or payload.get("pageSize") != 10
        ):
            raise LegalCoreApiError(502, "INVALID_LEGAL_CORE_RESPONSE", "Invalid response")
        return payload

    async def get_review_materials(
        self, telegram_user_id: int, *, page: int = 1, group: str | None = None
    ) -> dict[str, Any]:
        suffix = ""
        if group is not None:
            if group not in {
                "clinical",
                "labour",
                "courts",
                "privacy",
                "licensing",
                "healthcare",
                "general",
                "other",
            }:
                raise ValueError("review material group")
            suffix = f"&group={group}"
        payload = await self._editor_json(
            "GET", f"/v1/legal/review-materials?page={page}{suffix}", telegram_user_id
        )
        if (
            not isinstance(payload.get("items"), list)
            or payload.get("page") != page
            or payload.get("pageSize") != 10
            or not isinstance(payload.get("totalItems"), int)
        ):
            raise LegalCoreApiError(502, "INVALID_LEGAL_CORE_RESPONSE", "Invalid response")
        return payload

    async def get_editor_groups(
        self, telegram_user_id: int, *, page: int = 1, group: str | None = None
    ) -> dict[str, Any]:
        if group is not None and _EDITOR_CALLBACK_RE.fullmatch(f"editor:group:{group}:1") is None:
            raise ValueError("review group")
        path = "/v1/legal/editor/groups" + (f"/{group}" if group else "")
        return await self._editor_json("GET", f"{path}?page={page}", telegram_user_id)

    async def get_editor_preparation(self, actor_id: int, material_id: UUID) -> dict[str, Any]:
        return await self._editor_json(
            "GET", f"/v1/legal/review-materials/{material_id}/preparation", actor_id
        )

    async def get_group_preview(self, actor_id: int, group: str) -> dict[str, Any]:
        if _EDITOR_CALLBACK_RE.fullmatch(f"editor:batch:{group}") is None:
            raise ValueError("review group")
        return await self._editor_json(
            "GET", f"/v1/legal/editor/groups/{group}/approval-preview", actor_id
        )

    async def approve_group(
        self, actor_id: int, group: str, payload: dict[str, Any], key: UUID
    ) -> dict[str, Any]:
        if _EDITOR_CALLBACK_RE.fullmatch(f"editor:batch:{group}") is None:
            raise ValueError("review group")
        return await self._editor_json(
            "POST",
            f"/v1/legal/editor/groups/{group}/approval-events",
            actor_id,
            payload=payload,
            idempotency_key=key,
        )

    async def get_reference_review_preview(self, actor_id: int, group: str) -> dict[str, Any]:
        if _EDITOR_CALLBACK_RE.fullmatch(f"editor:refbatch:{group}") is None:
            raise ValueError("reference review group")
        return await self._editor_json(
            "GET", f"/v1/legal/editor/groups/{group}/reference-review-preview", actor_id
        )

    async def confirm_reference_review(
        self, actor_id: int, group: str, payload: dict[str, Any], key: UUID
    ) -> dict[str, Any]:
        if _EDITOR_CALLBACK_RE.fullmatch(f"editor:refbatch:{group}") is None:
            raise ValueError("reference review group")
        return await self._editor_json(
            "POST",
            f"/v1/legal/editor/groups/{group}/reference-review-events",
            actor_id,
            payload=payload,
            idempotency_key=key,
        )

    async def get_editor_version(self, telegram_user_id: int, version_id: UUID) -> dict[str, Any]:
        payload = await self._editor_json(
            "GET", f"/v1/legal/review-queue/{version_id}", telegram_user_id
        )
        if payload.get("versionId") != str(version_id):
            raise LegalCoreApiError(502, "INVALID_LEGAL_CORE_RESPONSE", "Invalid response")
        return payload

    async def get_editor_fragments(
        self, telegram_user_id: int, version_id: UUID, *, page: int
    ) -> dict[str, Any]:
        payload = await self._editor_json(
            "GET",
            f"/v1/legal/review-queue/{version_id}/fragments?page={page}",
            telegram_user_id,
        )
        if (
            not isinstance(payload.get("items"), list)
            or payload.get("page") != page
            or payload.get("pageSize") != 5
        ):
            raise LegalCoreApiError(502, "INVALID_LEGAL_CORE_RESPONSE", "Invalid response")
        return payload

    async def approve_editor_version(
        self,
        telegram_user_id: int,
        version_id: UUID,
        *,
        payload: dict[str, Any],
        idempotency_key: UUID,
    ) -> dict[str, Any]:
        response = await self._editor_json(
            "POST",
            f"/v1/legal/review-queue/{version_id}/approval-events",
            telegram_user_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        if (
            response.get("versionId") != str(version_id)
            or response.get("approvalState") != "APPROVED"
        ):
            raise LegalCoreApiError(502, "INVALID_LEGAL_CORE_RESPONSE", "Invalid response")
        return response

    async def download_editor_artifact(
        self, telegram_user_id: int, version_id: UUID
    ) -> tuple[bytes, str]:
        return await self._download_editor_file(telegram_user_id, version_id, resource="artifact")

    async def download_editor_excerpts(
        self, telegram_user_id: int, version_id: UUID
    ) -> tuple[bytes, str]:
        return await self._download_editor_file(telegram_user_id, version_id, resource="excerpts")

    async def download_editor_review_material(
        self, telegram_user_id: int, material_id: UUID
    ) -> tuple[bytes, str]:
        return await self._download_editor_url(
            telegram_user_id,
            f"/v1/legal/review-materials/{material_id}/artifact",
            allowed_mime_types={"application/pdf", "application/rtf"},
        )

    async def _download_editor_file(
        self, telegram_user_id: int, version_id: UUID, *, resource: str
    ) -> tuple[bytes, str]:
        return await self._download_editor_url(
            telegram_user_id,
            f"/v1/legal/review-queue/{version_id}/{resource}",
            allowed_mime_types={"application/pdf", "application/rtf", "text/plain"},
        )

    async def _download_editor_url(
        self,
        telegram_user_id: int,
        path: str,
        *,
        allowed_mime_types: set[str],
    ) -> tuple[bytes, str]:
        try:
            async with self._http.stream(
                "GET",
                path,
                headers=self._editor_headers(telegram_user_id),
            ) as response:
                if 300 <= response.status_code < 400:
                    raise LegalCoreApiError(
                        502, "LEGAL_CORE_REDIRECT_REJECTED", "Redirect rejected"
                    )
                if response.status_code >= 400:
                    raise LegalCoreApiError(
                        response.status_code,
                        "LEGAL_ARTIFACT_NOT_AVAILABLE",
                        "Artifact unavailable",
                    )
                mime_type = response.headers.get("content-type", "").split(";", 1)[0]
                if mime_type not in allowed_mime_types:
                    raise LegalCoreApiError(502, "INVALID_LEGAL_ARTIFACT", "Invalid artifact")
                raw_length = response.headers.get("content-length")
                if raw_length is not None:
                    try:
                        if int(raw_length) > _EDITOR_ARTIFACT_MAX_BYTES:
                            raise LegalCoreApiError(
                                502, "LEGAL_ARTIFACT_TOO_LARGE", "Artifact too large"
                            )
                    except ValueError as exc:
                        raise LegalCoreApiError(
                            502, "INVALID_LEGAL_ARTIFACT", "Invalid artifact"
                        ) from exc
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    data.extend(chunk)
                    if len(data) > _EDITOR_ARTIFACT_MAX_BYTES:
                        raise LegalCoreApiError(
                            502, "LEGAL_ARTIFACT_TOO_LARGE", "Artifact too large"
                        )
                content = bytes(data)
                expected_sha256 = response.headers.get("x-legal-artifact-sha256", "")
                if (
                    len(content) == 0
                    or (raw_length is not None and len(content) != int(raw_length))
                    or hashlib.sha256(content).hexdigest() != expected_sha256
                ):
                    raise LegalCoreApiError(502, "INVALID_LEGAL_ARTIFACT", "Invalid artifact")
                return content, mime_type
        except httpx2.HTTPError as exc:
            raise LegalCoreApiError(
                503, "LEGAL_CORE_UNAVAILABLE", "Legal Core unavailable"
            ) from exc


def _bounded(value: object, *, limit: int) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)[:limit]
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


def _verified_copy_source_label(source_url: str | None) -> str:
    hostname = urlsplit(source_url).hostname if source_url is not None else None
    return _VERIFIED_COPY_SOURCE_LABELS.get(hostname or "", "проверенная копия")


def _artifact_button_label(mime_type: object) -> str:
    return "📄 Открыть PDF" if mime_type == "application/pdf" else "📄 Открыть документ"


def _artifact_suffix(mime_type: str) -> str:
    return {
        "application/pdf": ".pdf",
        "application/rtf": ".rtf",
        "text/plain": ".txt",
    }[mime_type]


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
            back_keyboard(),
        )

    lines = [
        "📜 НОРМАТИВНАЯ БАЗА",
        "",
        f"Одобренные документы, из которых Legal Core может выбирать нормы на {as_of_date}.",
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
            str(fragment_count) if isinstance(fragment_count, int) and fragment_count > 0 else "—"
        )
        lines.extend(
            [
                f"• {title}",
                f"  {issuer}" + (f" · № {official_number}" if official_number != "—" else ""),
                f"  {_applicability(raw_item.get('effectiveFrom'), raw_item.get('effectiveTo'))}",
                f"  Фрагментов: {count_text} · SHA-256: {_short_sha(raw_item.get('rawSha256'))}",
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
    buttons.extend([list(row) for row in back_keyboard().inline_keyboard])
    return _bounded_message("\n".join(lines)), InlineKeyboardMarkup(buttons)


def _editor_version_id(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError("editor version id")
    return UUID(value)


def _editor_page(value: object) -> int:
    if not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("editor page")
    return value


def _editor_pagination(
    *, page: int, page_size: int, total_items: int, callback_prefix: str
) -> list[list[InlineKeyboardButton]]:
    if total_items < 0:
        raise ValueError("editor total")
    rows: list[list[InlineKeyboardButton]] = []
    buttons: list[InlineKeyboardButton] = []
    if page > 1:
        buttons.append(
            InlineKeyboardButton("◀️ Назад", callback_data=f"{callback_prefix}:{page - 1}")
        )
    if page * page_size < total_items:
        buttons.append(
            InlineKeyboardButton("Вперёд ▶️", callback_data=f"{callback_prefix}:{page + 1}")
        )
    if buttons:
        rows.append(buttons)
    return rows


def render_platform_review_queue(payload: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    """Render public candidate metadata; document bytes require a separate editor action."""

    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("review queue items")
    labels = {
        "REVIEW_REQUIRED": "🟡 ОЖИДАЕТ ПРОВЕРКИ",
        "APPROVED": "✅ ОДОБРЕН",
        "BLOCKED": "⛔ ЗАБЛОКИРОВАН",
    }
    lines = [
        "⚖️ ПРОВЕРКА НОРМ",
        "",
        "Откройте кандидат, проверьте документ и подтвердите все четыре аттестации.",
        "",
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for item in raw_items[:_MAX_DOCUMENTS]:
        if not isinstance(item, dict):
            continue
        try:
            version_id = _editor_version_id(item.get("versionId"))
        except ValueError:
            continue
        state = item.get("approvalState")
        state_label = labels.get(state if isinstance(state, str) else "", "⚪ НЕИЗВЕСТНЫЙ СТАТУС")
        eligible = item.get("approvalEligible") is True
        eligibility_label = (
            "проверка доступности — при открытии карточки"
            if item.get("approvalPreflightChecked") is False
            else "можно проверить и подтвердить"
            if eligible
            else "старая/недоступная версия"
        )
        artifact_kind = _bounded(item.get("artifactKind"), limit=30)
        artifact_label = (
            "копия КонсультантПлюс"
            if artifact_kind == "THIRD_PARTY_VERIFIED_COPY"
            else artifact_kind
        )
        lines.extend(
            [
                state_label,
                f"• {_bounded(item.get('documentTitle'), limit=180)}",
                f"  № {_bounded(item.get('officialNumber'), limit=80)} · {artifact_label}",
                "  " + eligibility_label,
                "",
            ]
        )
        detail_page = _editor_page(payload.get("page", 1))
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🔎 Открыть № {_bounded(item.get('officialNumber'), limit=30)}"[:64],
                    callback_data=f"editor:detail:{version_id}:{detail_page}",
                )
            ]
        )
    if not raw_items:
        lines.append("В очереди пока нет документов.")
    page = _editor_page(payload.get("page", 1))
    page_size = payload.get("pageSize", 10)
    total_items = payload.get("totalItems", len(raw_items))
    if not isinstance(page_size, int) or not isinstance(total_items, int):
        raise ValueError("review queue page metadata")
    buttons.extend(
        _editor_pagination(
            page=page,
            page_size=page_size,
            total_items=total_items,
            callback_prefix="editor:page",
        )
    )
    buttons.append(
        [InlineKeyboardButton("📥 Загруженные материалы", callback_data="editor:materials:1")]
    )
    buttons.extend([list(row) for row in back_keyboard().inline_keyboard])
    return _bounded_message("\n".join(lines)), InlineKeyboardMarkup(buttons)


def render_editor_review_materials(
    payload: dict[str, Any], *, return_to_queue: bool = True
) -> tuple[str, InlineKeyboardMarkup]:
    """Render seven groups simply; a material title appears only on its button."""

    del return_to_queue
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("review material items")
    page = _editor_page(payload.get("page"))
    page_size = payload.get("pageSize")
    total_items = payload.get("totalItems")
    if not isinstance(page_size, int) or not isinstance(total_items, int):
        raise ValueError("review material page metadata")
    selected_group = payload.get("selectedGroup")
    groups = payload.get("groups", [])
    if not isinstance(groups, list):
        raise ValueError("review material groups")

    buttons: list[list[InlineKeyboardButton]] = []
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("review material group")
        key = group.get("key")
        callback = f"editor:group:{key}:1"
        if _EDITOR_CALLBACK_RE.fullmatch(callback) is None:
            raise ValueError("review material group key")
        if selected_group is None:
            group_label = (
                f"📂 {_bounded(group.get('title'), limit=43)} ({group.get('totalItems', 0)})"
            )
            buttons.append([InlineKeyboardButton(group_label[:64], callback_data=callback)])

    if selected_group is None:
        buttons.extend([list(row) for row in back_keyboard().inline_keyboard])
        return (
            "⚖️ ПРОВЕРКА НОРМ\n\nВыберите группу документов.\n"
            "Внутри — документы для просмотра и проверки.",
            InlineKeyboardMarkup(buttons),
        )
    if _EDITOR_CALLBACK_RE.fullmatch(f"editor:group:{selected_group}:1") is None:
        raise ValueError("selected review material group")
    reference_count = payload.get("referenceReviewableCount", 0)
    if not isinstance(reference_count, int) or reference_count < 0:
        raise ValueError("reference review count")

    lines = [
        "⚖️ ПРОВЕРКА МАТЕРИАЛОВ",
        f"Материалов: {total_items}. Страница {page}.",
        "Новые материалы не одобрены и не участвуют в рекомендациях до отдельного подтверждения.",
        "Для неподготовленных документов требуются реквизиты и проверка.",
        "Откройте нужный материал кнопкой ниже.",
    ]
    for item in raw_items[:_MAX_DOCUMENTS]:
        if not isinstance(item, dict):
            continue
        try:
            item_id = _editor_version_id(item.get("versionId") or item.get("materialId"))
        except ValueError:
            continue
        buttons.append(
            [
                InlineKeyboardButton(
                    f"📄 Открыть: {_bounded(item.get('title'), limit=40)}"[:64],
                    callback_data=(
                        f"editor:detail:{item_id}:1"
                        if item.get("versionId")
                        else f"editor:preparation:{item_id}"
                        if item.get("preparationId")
                        else f"editor:material:{item_id}"
                    ),
                )
            ]
        )
    if not raw_items:
        lines.append("В этой группе пока нет материалов.")
    buttons.extend(
        _editor_pagination(
            page=page,
            page_size=page_size,
            total_items=total_items,
            callback_prefix=f"editor:group:{selected_group}",
        )
    )
    if reference_count:
        buttons.append(
            [
                InlineKeyboardButton(
                    "✅ Подтвердить справочные материалы",
                    callback_data=f"editor:refbatch:{selected_group}",
                )
            ]
        )
    if selected_group != "clinical":
        buttons.append(
            [
                InlineKeyboardButton(
                    "✅ Утвердить нормы",
                    callback_data=f"editor:batch:{selected_group}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton("← К группам", callback_data="editor:materials:1")])
    return _bounded_message("\n".join(lines)), InlineKeyboardMarkup(buttons)


def render_material_preparation(payload: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    material_id = _editor_version_id(payload.get("materialId"))
    back = f"editor:group:{payload.get('groupKey')}:1"
    if _EDITOR_CALLBACK_RE.fullmatch(back) is None:
        raise ValueError("preparation group")
    normative = payload.get("kind") == "NORMATIVE"
    lines = [
        _bounded(payload.get("title"), limit=1000),
        "",
        "Правовой документ — подготовка к проверке."
        if normative
        else "Справочный материал — не нормативный акт.",
        "Эта карточка не означает утверждение документа.",
    ]
    if not normative:
        lines.append(f"Год: {payload.get('referenceYear') or 'не указан'}")
    limitations = payload.get("limitations", [])
    missing = payload.get("missingFields", [])
    if not isinstance(limitations, list) or not isinstance(missing, list):
        raise ValueError("preparation details")
    if limitations:
        lines.extend(["", "Ограничения извлечённого текста:"])
        lines.extend(f"• {_bounded(item, limit=250)}" for item in limitations[:6])
    if missing:
        lines.extend(["", "Не все реквизиты подготовлены для утверждения норм."])
    lines.extend(["", "Для полной проверки откройте исходный документ ниже."])
    return _bounded_message("\n".join(lines)), InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📄 Скачать оригинал", callback_data=f"editor:material:{material_id}"
                )
            ],
            [InlineKeyboardButton("← Назад к группе", callback_data=back)],
        ]
    )


def render_group_approval_preview(
    preview: dict[str, Any], *, batch_id: str, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    _editor_version_id(batch_id)
    page = _editor_page(page)
    group = preview.get("group")
    if _EDITOR_CALLBACK_RE.fullmatch(f"editor:batch:{group}") is None:
        raise ValueError("review group")
    ready, blocked = preview.get("ready"), preview.get("blocked")
    if not isinstance(ready, list) or not isinstance(blocked, list):
        raise ValueError("review group preview")
    if len(ready) > 200 or len(blocked) > 200:
        raise ValueError("review group bounds")
    lines = [
        "✅ ПОДТВЕРЖДЕНИЕ ГРУППЫ",
        f"К утверждению: {len(ready)}.",
        f"Ранее утверждено: {preview.get('alreadyApproved', 0)}.",
        "",
    ]
    for item in ready[(page - 1) * 10 : page * 10]:
        effective_to = (
            _bounded(item.get("effectiveTo"), limit=10) if item.get("effectiveTo") else "—"
        )
        lines.append(
            f"• {_bounded(item.get('title'), limit=100)}\n"
            f"  Действует с {_bounded(item.get('effectiveFrom'), limit=10)}; "
            f"до {effective_to}"
        )
    if blocked:
        lines.extend(["", f"Не будут утверждены: {len(blocked)}."])
        for item in blocked[:4]:
            reason = item.get("reasonCode")
            label = (
                "нужны реквизиты и подготовка версии"
                if reason == "METADATA_REQUIRED"
                else "справочный материал, не нормативный акт"
                if reason == "CLINICAL_REFERENCE_NOT_LEGAL_VERSION"
                else "версия не прошла проверку; откройте карточку"
            )
            lines.append(f"• {_bounded(item.get('title'), limit=90)} — {label}")
        if len(blocked) > 4:
            lines.append(f"И ещё {len(blocked) - 4}; они остаются в списке группы.")
    buttons = _editor_pagination(
        page=page,
        page_size=10,
        total_items=len(ready),
        callback_prefix=f"editor:batchpage:{batch_id}",
    )
    if ready and (page - 1) * 10 < len(ready) <= page * 10:
        lines.extend(
            [
                "",
                "Подтверждаю проверку всех перечисленных документов: источник и "
                "соответствие оригиналу, полноту текста, даты действия и фрагменты.",
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    f"✅ Подтверждаю — утвердить {len(ready)}",
                    callback_data=f"editor:batchconfirm:{batch_id}",
                )
            ]
        )
    elif not ready:
        lines.extend(["", "Готовых к утверждению документов пока нет."])
    buttons.append(
        [InlineKeyboardButton("← К документам", callback_data=f"editor:group:{group}:1")]
    )
    return _bounded_message("\n".join(lines)), InlineKeyboardMarkup(buttons)


def render_reference_review_preview(
    preview: dict[str, Any], *, batch_id: str, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    _editor_version_id(batch_id)
    group = preview.get("group")
    if _EDITOR_CALLBACK_RE.fullmatch(f"editor:refbatch:{group}") is None:
        raise ValueError("reference review group")
    ready = preview.get("ready")
    if not isinstance(ready, list) or len(ready) > 200:
        raise ValueError("reference review preview")
    page = _editor_page(page)
    lines = [
        "✅ ПОДТВЕРЖДЕНИЕ СПРАВОЧНЫХ МАТЕРИАЛОВ",
        f"К подтверждению: {len(ready)}.",
        f"Ранее подтверждено: {preview.get('alreadyReviewed', 0)}.",
        "",
    ]
    for item in ready[(page - 1) * 10 : page * 10]:
        if not isinstance(item, dict):
            raise ValueError("reference review item")
        lines.append(f"• {_bounded(item.get('title'), limit=100)}")
    buttons = _editor_pagination(
        page=page,
        page_size=10,
        total_items=len(ready),
        callback_prefix=f"editor:refbatchpage:{batch_id}",
    )
    if ready and (page - 1) * 10 < len(ready) <= page * 10:
        lines.extend(
            [
                "",
                "Подтверждаю: исходники просмотрены; это справочные материалы, не законы "
                "и не самостоятельное юридическое основание.",
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    f"✅ Подтвердить {len(ready)} материалов",
                    callback_data=f"editor:refconfirm:{batch_id}",
                )
            ]
        )
    elif not ready:
        lines.extend(["", "Неподтверждённых справочных материалов в группе нет."])
    buttons.append(
        [InlineKeyboardButton("← К документам", callback_data=f"editor:group:{group}:1")]
    )
    return _bounded_message("\n".join(lines)), InlineKeyboardMarkup(buttons)


def _pending_editor_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    data = context.user_data
    if data is None:
        return None
    pending = data.get(_EDITOR_PENDING_KEY)
    return pending if isinstance(pending, dict) else None


def _new_editor_state(detail: dict[str, Any]) -> dict[str, Any]:
    version_id = _editor_version_id(detail.get("versionId"))
    expected = {
        "expectedSha256": detail.get("rawSha256"),
        "expectedNormalizedSha256": detail.get("normalizedSha256"),
        "expectedFragmentsSha256": detail.get("fragmentsSha256"),
        "expectedEffectiveFrom": detail.get("effectiveFrom"),
        "expectedEffectiveTo": detail.get("effectiveTo"),
    }
    if not all(
        isinstance(value, str) and value for value in expected.values() if value is not None
    ):
        raise ValueError("editor version identity")
    return {
        "versionId": str(version_id),
        "artifactKind": detail.get("artifactKind"),
        "expected": expected,
        "attestations": {
            "source": False,
            "artifact": False,
            "dates": False,
            "fragments": False,
        },
        "idempotencyKey": str(uuid4()),
    }


def _attestation_label(state: dict[str, Any], key: str, label: str) -> str:
    raw = state.get("attestations")
    checked = isinstance(raw, dict) and raw.get(key) is True
    return f"{'✅' if checked else '☐'} {label}"


def render_editor_version_detail(
    detail: dict[str, Any], state: dict[str, Any]
) -> tuple[str, InlineKeyboardMarkup]:
    version_id = _editor_version_id(detail.get("versionId"))
    source_url = _official_url(detail.get("sourceUrl"))
    third_party_copy = detail.get("artifactKind") == "THIRD_PARTY_VERIFIED_COPY"
    source_label = _verified_copy_source_label(source_url)
    artifact_is_pdf = detail.get("rawMimeType") == "application/pdf"
    approval_eligible = detail.get("approvalEligible") is True
    approval_state = _bounded(detail.get("approvalState"), limit=30)
    lines = [
        "⚖️ КАРТОЧКА НОРМЫ",
        "",
        _bounded(detail.get("documentTitle"), limit=500),
        f"Издатель: {_bounded(detail.get('issuer'), limit=240)}",
        f"Номер: {_bounded(detail.get('officialNumber'), limit=80)}",
        f"Статус: {approval_state}",
        (
            f"Источник: {source_label} — проверенная копия, не первичная публикация."
            if third_party_copy
            else "Источник: официальная публикация."
        ),
        "Артефакт: "
        f"{_bounded(detail.get('rawMimeType'), limit=80)} · "
        f"{_bounded(detail.get('rawSizeBytes'), limit=30)} байт",
        f"Страниц: {_bounded(detail.get('artifactPageCount'), limit=20)}",
        _applicability(detail.get("effectiveFrom"), detail.get("effectiveTo")),
        f"Получен: {_bounded(detail.get('artifactRetrievedAt'), limit=32)}",
        f"SHA raw: {_short_sha(detail.get('rawSha256'))}",
        f"SHA text: {_short_sha(detail.get('normalizedSha256'))}",
        f"SHA fragments: {_short_sha(detail.get('fragmentsSha256'))}",
        f"Выбранных фрагментов: {_bounded(detail.get('fragmentCount'), limit=12)}",
        (
            "Сначала прочитайте PDF, затем сверьте полные выбранные выдержки с его текстом."
            if artifact_is_pdf
            else (
                "Сначала прочитайте документ, затем сверьте полные выбранные выдержки "
                "с его текстом."
            )
        ),
        (
            "Выгрузка содержит статьи/пункты; страницы PDF для выдержек не размечены."
            if artifact_is_pdf
            else "Выгрузка содержит статьи/пункты; сверяйте их с исходным документом."
        ),
        "",
        (
            "Подтверждение доступно после всех четырёх ручных проверок."
            if approval_eligible
            else "Эта версия не может быть одобрена через рабочее место."
        ),
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    if source_url is not None:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🌐 Источник: {source_label}"
                    if third_party_copy
                    else "🌐 Официальный источник",
                    url=source_url,
                )
            ]
        )
    buttons.extend(
        [
            [
                InlineKeyboardButton(
                    _artifact_button_label(detail.get("rawMimeType")),
                    callback_data=f"editor:artifact:{version_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "📑 Полные выдержки для проверки", callback_data=f"editor:excerpts:{version_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    _attestation_label(
                        state,
                        "source",
                        (
                            "Сверил копию с официальным текстом и редакцией"
                            if third_party_copy
                            else "Источник — официальная публикация"
                        ),
                    ),
                    callback_data=f"editor:attest:{version_id}:source",
                )
            ],
            [
                InlineKeyboardButton(
                    _attestation_label(state, "artifact", "Документ полный"),
                    callback_data=f"editor:attest:{version_id}:artifact",
                )
            ],
            [
                InlineKeyboardButton(
                    _attestation_label(state, "dates", "Даты действия проверены"),
                    callback_data=f"editor:attest:{version_id}:dates",
                )
            ],
            [
                InlineKeyboardButton(
                    _attestation_label(state, "fragments", "Фрагменты проверены"),
                    callback_data=f"editor:attest:{version_id}:fragments",
                )
            ],
        ]
    )
    attestations = state.get("attestations")
    if (
        approval_eligible
        and isinstance(attestations, dict)
        and all(
            attestations.get(key) is True for key in ("source", "artifact", "dates", "fragments")
        )
    ):
        buttons.append(
            [
                InlineKeyboardButton(
                    "✅ Одобрить версию", callback_data=f"editor:confirm:{version_id}"
                )
            ]
        )
    buttons.append([InlineKeyboardButton("← К списку", callback_data="editor:open")])
    buttons.extend([list(row) for row in back_keyboard().inline_keyboard])
    return _bounded_message("\n".join(lines)), InlineKeyboardMarkup(buttons)


def render_editor_fragments(
    payload: dict[str, Any], *, version_id: UUID
) -> tuple[str, InlineKeyboardMarkup]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("editor fragments")
    page = _editor_page(payload.get("page"))
    page_size = payload.get("pageSize")
    total_items = payload.get("totalItems")
    if not isinstance(page_size, int) or not isinstance(total_items, int):
        raise ValueError("editor fragment page")
    lines = ["📑 ВЫБРАННЫЕ ФРАГМЕНТЫ", ""]
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"{_bounded(item.get('structuralPath'), limit=300)} · "
                f"#{_bounded(item.get('ordinal'), limit=12)}",
                _bounded(item.get("fragmentText"), limit=1_200),
                f"SHA-256: {_short_sha(item.get('textSha256'))}"
                + (" · сокращено" if item.get("truncated") is True else ""),
                "",
            ]
        )
    if not raw_items:
        lines.append("На этой странице фрагментов нет.")
    buttons = _editor_pagination(
        page=page,
        page_size=page_size,
        total_items=total_items,
        callback_prefix=f"editor:fragments:{version_id}",
    )
    buttons.append(
        [InlineKeyboardButton("← К карточке", callback_data=f"editor:detail:{version_id}:1")]
    )
    buttons.extend([list(row) for row in back_keyboard().inline_keyboard])
    return _bounded_message("\n".join(lines)), InlineKeyboardMarkup(buttons)


async def _editor_reply(update: Update, text: str, keyboard: InlineKeyboardMarkup) -> None:
    if update.effective_message is not None:
        await update.effective_message.reply_text(text, reply_markup=keyboard)


async def show_platform_review_queue(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, page: int = 1
) -> None:
    # Legacy entry points now lead to the same group directory, never a second queue.
    await show_editor_review_materials(update, context)


async def show_editor_review_materials(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, page: int = 1, group: str | None = None
) -> None:
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None:
        return
    client = LegalLibraryClient()
    try:
        text, keyboard = render_editor_review_materials(
            await client.get_editor_groups(actor_id, page=page, group=group)
        )
    except (LegalCoreApiError, ValueError) as exc:
        code = exc.code if isinstance(exc, LegalCoreApiError) else type(exc).__name__
        logger.warning("legal review materials load failed: %s", code)
        await gateway_bot._reply(
            update, "⚠️ Не удалось открыть материалы для проверки. Попробуйте ещё раз."
        )
        return
    finally:
        await client.aclose()
    await _editor_reply(update, text, keyboard)


async def _show_material_preparation(update: Update, *, material_id: UUID) -> None:
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None:
        return
    client = LegalLibraryClient()
    try:
        text, keyboard = render_material_preparation(
            await client.get_editor_preparation(actor_id, material_id)
        )
    except (LegalCoreApiError, ValueError) as exc:
        logger.warning("material preparation load failed: %s", type(exc).__name__)
        await gateway_bot._reply(
            update, "⚠️ Не удалось открыть карточку. Вернитесь к группе и попробуйте ещё раз."
        )
        return
    finally:
        await client.aclose()
    await _editor_reply(update, text, keyboard)


async def _show_editor_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, version_id: UUID, reset: bool
) -> None:
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None:
        return
    client = LegalLibraryClient()
    try:
        detail = await client.get_editor_version(actor_id, version_id)
        fresh_state = _new_editor_state(detail)
        pending = fresh_state if reset else _pending_editor_state(context)
        if (
            pending is None
            or pending.get("versionId") != str(version_id)
            or pending.get("expected") != fresh_state["expected"]
        ):
            pending = fresh_state
        if context.user_data is not None:
            context.user_data[_EDITOR_PENDING_KEY] = pending
        text, keyboard = render_editor_version_detail(detail, pending)
    except (LegalCoreApiError, ValueError) as exc:
        logger.warning("legal editor detail load failed: %s", type(exc).__name__)
        await gateway_bot._reply(
            update, "⚠️ Не удалось открыть версию. Вернитесь к списку и попробуйте ещё раз."
        )
        return
    finally:
        await client.aclose()
    await _editor_reply(update, text, keyboard)


async def _show_editor_fragments(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, version_id: UUID, page: int
) -> None:
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None:
        return
    client = LegalLibraryClient()
    try:
        text, keyboard = render_editor_fragments(
            await client.get_editor_fragments(actor_id, version_id, page=page),
            version_id=version_id,
        )
    except (LegalCoreApiError, ValueError) as exc:
        logger.warning("legal editor fragments load failed: %s", type(exc).__name__)
        await gateway_bot._reply(update, "⚠️ Не удалось открыть фрагменты. Попробуйте ещё раз.")
        return
    finally:
        await client.aclose()
    await _editor_reply(update, text, keyboard)


async def _send_editor_artifact(
    update: Update, *, version_id: UUID, excerpts: bool = False
) -> None:
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None or update.effective_message is None:
        return
    client = LegalLibraryClient()
    try:
        content, mime_type = await (
            client.download_editor_excerpts(actor_id, version_id)
            if excerpts
            else client.download_editor_artifact(actor_id, version_id)
        )
        prefix = "legal-excerpts" if excerpts else "legal"
        filename = f"{prefix}-{version_id}{_artifact_suffix(mime_type)}"
        await update.effective_message.reply_document(
            document=InputFile(BytesIO(content), filename=filename),
            caption=(
                "Полные выбранные выдержки. Сверьте с исходным документом до "
                "подтверждения проверки."
                if excerpts
                else "Сохранённая неизменяемая версия документа для юридической проверки."
            ),
            reply_markup=_editor_return_keyboard(version_id),
        )
    except LegalCoreApiError as exc:
        logger.warning("legal editor artifact failed: %s", exc.code)
        await gateway_bot._reply(
            update,
            "⚠️ Документ не удалось загрузить. Попробуйте позже.",
            reply_markup=_editor_return_keyboard(version_id),
        )
    finally:
        await client.aclose()


def _editor_return_keyboard(version_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("← К карточке", callback_data=f"editor:detail:{version_id}:1")],
            [InlineKeyboardButton("← К группам", callback_data="editor:open")],
        ]
    )


def _editor_material_return_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("← К группам", callback_data="editor:materials:1")],
        ]
    )


async def _send_editor_review_material(update: Update, *, material_id: UUID) -> None:
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None or update.effective_message is None:
        return
    client = LegalLibraryClient()
    try:
        content, mime_type = await client.download_editor_review_material(actor_id, material_id)
        await update.effective_message.reply_document(
            document=InputFile(
                BytesIO(content),
                filename=f"review-material-{material_id}{_artifact_suffix(mime_type)}",
            ),
            caption=(
                "Неодобренный исходный материал. Укажите реквизиты, сверьте источник и "
                "создайте версию для отдельного утверждения."
            ),
            reply_markup=_editor_material_return_keyboard(),
        )
    except LegalCoreApiError as exc:
        logger.warning("legal review material delivery failed: %s", exc.code)
        await gateway_bot._reply(
            update,
            "⚠️ Материал не удалось загрузить. Попробуйте позже.",
            reply_markup=_editor_material_return_keyboard(),
        )
    finally:
        await client.aclose()


async def _start_editor_delivery(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, version_id: UUID, excerpts: bool
) -> None:
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None or update.effective_message is None:
        return
    queue = context.application.bot_data.get(_EDITOR_DELIVERY_KEY)
    if not isinstance(queue, EditorFileDeliveryQueue):
        queue = EditorFileDeliveryQueue(context.application)
        context.application.bot_data[_EDITOR_DELIVERY_KEY] = queue
    keyboard = _editor_return_keyboard(version_id)

    async def deliver() -> None:
        await gateway_bot._reply(
            update,
            "📥 Готовлю файл. Остальные меню доступны во время загрузки.",
            reply_markup=keyboard,
        )
        await _send_editor_artifact(update, version_id=version_id, excerpts=excerpts)

    async def report_error() -> None:
        await gateway_bot._reply(
            update,
            "⚠️ Файл не удалось доставить вовремя. Нажмите кнопку загрузки ещё раз.",
            reply_markup=keyboard,
        )

    admission = queue.submit(actor_id, deliver, report_error)
    if admission != "STARTED":
        message = (
            "📥 Ваш файл уже загружается. Дождитесь завершения; меню доступны."
            if admission == "DUPLICATE"
            else "⚠️ Загрузка временно занята. Попробуйте чуть позже; меню доступны."
        )
        await gateway_bot._reply(update, message, reply_markup=keyboard)


async def _start_editor_material_delivery(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, material_id: UUID
) -> None:
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None or update.effective_message is None:
        return
    queue = context.application.bot_data.get(_EDITOR_DELIVERY_KEY)
    if not isinstance(queue, EditorFileDeliveryQueue):
        queue = EditorFileDeliveryQueue(context.application)
        context.application.bot_data[_EDITOR_DELIVERY_KEY] = queue
    keyboard = _editor_material_return_keyboard()

    async def deliver() -> None:
        await gateway_bot._reply(
            update,
            "📥 Готовлю исходный файл. Остальные меню доступны во время загрузки.",
            reply_markup=keyboard,
        )
        await _send_editor_review_material(update, material_id=material_id)

    async def report_error() -> None:
        await gateway_bot._reply(
            update,
            "⚠️ Файл не удалось доставить вовремя. Нажмите кнопку загрузки ещё раз.",
            reply_markup=keyboard,
        )

    admission = queue.submit(actor_id, deliver, report_error)
    if admission != "STARTED":
        message = (
            "📥 Ваш файл уже загружается. Дождитесь завершения; меню доступны."
            if admission == "DUPLICATE"
            else "⚠️ Загрузка временно занята. Попробуйте чуть позже; меню доступны."
        )
        await gateway_bot._reply(update, message, reply_markup=keyboard)


async def _confirm_editor_approval(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, version_id: UUID
) -> None:
    actor_id = gateway_bot._actor_id(update)
    pending = _pending_editor_state(context)
    if actor_id is None or pending is None or pending.get("versionId") != str(version_id):
        await gateway_bot._reply(update, "⚠️ Откройте версию заново и подтвердите проверки.")
        return
    attestations = pending.get("attestations")
    expected = pending.get("expected")
    if (
        not isinstance(attestations, dict)
        or not isinstance(expected, dict)
        or not all(
            attestations.get(key) is True for key in ("source", "artifact", "dates", "fragments")
        )
    ):
        await gateway_bot._reply(update, "⚠️ Подтвердите все четыре проверки перед одобрением.")
        return
    try:
        idempotency_key = UUID(str(pending.get("idempotencyKey")))
        request = {
            **expected,
            "sourceIsOfficial": pending.get("artifactKind") == "OFFICIAL_RAW",
            "officialTextCompared": True,
            "artifactIsComplete": True,
            "effectiveDatesVerified": True,
            "fragmentsVerified": True,
        }
        client = LegalLibraryClient()
        try:
            result = await client.approve_editor_version(
                actor_id,
                version_id,
                payload=request,
                idempotency_key=idempotency_key,
            )
        finally:
            await client.aclose()
    except (LegalCoreApiError, ValueError) as exc:
        code = exc.code if isinstance(exc, LegalCoreApiError) else type(exc).__name__
        logger.warning("legal editor approval failed: %s", code)
        await gateway_bot._reply(
            update,
            "⚠️ Версия не одобрена: она могла измениться или не пройти серверную проверку. "
            "Откройте её заново.",
        )
        return
    if context.user_data is not None:
        context.user_data.pop(_EDITOR_PENDING_KEY, None)
    await _editor_reply(
        update,
        f"✅ Версия одобрена. Время решения: {_bounded(result.get('approvedAt'), limit=32)}.",
        back_keyboard(),
    )


async def legal_editor_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    callback_data = await gateway_bot._answer_callback(update)
    if callback_data is None or _EDITOR_CALLBACK_RE.fullmatch(callback_data) is None:
        raise ApplicationHandlerStop
    try:
        if callback_data == "editor:open":
            await show_platform_review_queue(update, context)
        elif callback_data.startswith("editor:page:"):
            await show_platform_review_queue(
                update, context, page=int(callback_data.rsplit(":", 1)[1])
            )
        elif callback_data.startswith("editor:materials:"):
            await show_editor_review_materials(
                update, context, page=int(callback_data.rsplit(":", 1)[1])
            )
        elif callback_data.startswith("editor:group:"):
            _, _, group, raw_page = callback_data.split(":")
            await show_editor_review_materials(update, context, page=int(raw_page), group=group)
        elif callback_data.startswith("editor:refbatch:"):
            await _show_reference_review(update, context, group=callback_data.rsplit(":", 1)[1])
        elif callback_data.startswith("editor:refbatchpage:"):
            _, _, batch_id, raw_page = callback_data.split(":")
            pending = (context.user_data or {}).get(_EDITOR_REFERENCE_PENDING_KEY)
            if isinstance(pending, dict) and pending.get("id") == batch_id:
                page = _editor_page(int(raw_page))
                text, keyboard = render_reference_review_preview(
                    pending["preview"], batch_id=batch_id, page=page
                )
                if (page - 1) * 10 < len(pending["ids"]) <= page * 10:
                    pending["lastPageShown"] = True
                await _editor_reply(update, text, keyboard)
            else:
                await gateway_bot._reply(
                    update, "Откройте подтверждение справочных материалов заново."
                )
        elif callback_data.startswith("editor:refconfirm:"):
            await _confirm_reference_review(
                update, context, batch_id=callback_data.rsplit(":", 1)[1]
            )
        elif callback_data.startswith("editor:batch:"):
            await _show_group_approval(update, context, group=callback_data.rsplit(":", 1)[1])
        elif callback_data.startswith("editor:batchpage:"):
            _, _, batch_id, raw_page = callback_data.split(":")
            pending = (context.user_data or {}).get(_EDITOR_GROUP_PENDING_KEY)
            if isinstance(pending, dict) and pending.get("id") == batch_id:
                page = _editor_page(int(raw_page))
                text, keyboard = render_group_approval_preview(
                    pending["preview"], batch_id=batch_id, page=page
                )
                if (page - 1) * 10 < len(pending["ids"]) <= page * 10:
                    pending["lastPageShown"] = True
                await _editor_reply(update, text, keyboard)
            else:
                await gateway_bot._reply(update, "Откройте подтверждение группы заново.")
        elif callback_data.startswith("editor:batchconfirm:"):
            await _confirm_group_approval(update, context, batch_id=callback_data.rsplit(":", 1)[1])
        elif callback_data.startswith("editor:detail:"):
            _, _, raw_version_id, _ = callback_data.split(":")
            await _show_editor_detail(update, context, version_id=UUID(raw_version_id), reset=False)
        elif callback_data.startswith("editor:artifact:"):
            await _start_editor_delivery(
                update, context, version_id=UUID(callback_data.rsplit(":", 1)[1]), excerpts=False
            )
        elif callback_data.startswith("editor:preparation:"):
            await _show_material_preparation(
                update, material_id=UUID(callback_data.rsplit(":", 1)[1])
            )
        elif callback_data.startswith("editor:material:"):
            await _start_editor_material_delivery(
                update, context, material_id=UUID(callback_data.rsplit(":", 1)[1])
            )
        elif callback_data.startswith("editor:excerpts:"):
            await _start_editor_delivery(
                update, context, version_id=UUID(callback_data.rsplit(":", 1)[1]), excerpts=True
            )
        elif callback_data.startswith("editor:fragments:"):
            _, _, raw_version_id, raw_page = callback_data.split(":")
            await _show_editor_fragments(
                update,
                context,
                version_id=UUID(raw_version_id),
                page=int(raw_page),
            )
        elif callback_data.startswith("editor:attest:"):
            _, _, raw_version_id, key = callback_data.split(":")
            version_id = UUID(raw_version_id)
            pending = _pending_editor_state(context)
            if (
                pending is None
                or pending.get("versionId") != str(version_id)
                or not isinstance(pending.get("attestations"), dict)
            ):
                await gateway_bot._reply(update, "⚠️ Откройте версию заново и повторите проверку.")
            else:
                pending["attestations"][key] = not bool(pending["attestations"].get(key))
                await _show_editor_detail(update, context, version_id=version_id, reset=False)
        elif callback_data.startswith("editor:confirm:"):
            await _confirm_editor_approval(
                update, context, version_id=UUID(callback_data.rsplit(":", 1)[1])
            )
    except (ValueError, IndexError):
        await gateway_bot._reply(update, "⚠️ Некорректное действие проверки.")
    raise ApplicationHandlerStop


async def _show_reference_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, group: str
) -> None:
    actor = gateway_bot._actor_id(update)
    if actor is None or context.user_data is None:
        return
    client = LegalLibraryClient()
    try:
        preview = await client.get_reference_review_preview(actor, group)
        snapshot = preview.get("snapshot")
        ready = preview.get("ready")
        if (
            not isinstance(snapshot, str)
            or re.fullmatch(r"[0-9a-f]{64}", snapshot) is None
            or not isinstance(ready, list)
        ):
            raise ValueError("reference review snapshot")
        batch_id = str(uuid4())
        text, keyboard = render_reference_review_preview(preview, batch_id=batch_id, page=1)
        ids = [str(_editor_version_id(item.get("preparationId"))) for item in ready]
        context.user_data[_EDITOR_REFERENCE_PENDING_KEY] = {
            "id": batch_id,
            "group": group,
            "preview": preview,
            "ids": ids,
            "lastPageShown": len(ids) <= 10,
        }
    except (LegalCoreApiError, ValueError):
        await gateway_bot._reply(
            update,
            "Не удалось подготовить подтверждение справочных материалов. Откройте группу снова.",
        )
        return
    finally:
        await client.aclose()
    await _editor_reply(update, text, keyboard)


async def _confirm_reference_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, batch_id: str
) -> None:
    actor = gateway_bot._actor_id(update)
    pending = (context.user_data or {}).get(_EDITOR_REFERENCE_PENDING_KEY)
    if (
        actor is None
        or not isinstance(pending, dict)
        or pending.get("id") != batch_id
        or not pending.get("lastPageShown")
        or not pending.get("ids")
    ):
        await gateway_bot._reply(update, "Откройте подтверждение и просмотрите список материалов.")
        return
    client = LegalLibraryClient()
    try:
        result = await client.confirm_reference_review(
            actor,
            pending["group"],
            {
                "expectedSnapshot": pending["preview"]["snapshot"],
                "preparationIds": pending["ids"],
                "originalReviewed": True,
                "referenceOnlyUnderstood": True,
            },
            UUID(batch_id),
        )
    except LegalCoreApiError as exc:
        message = (
            "Список или состояние материалов изменились. Откройте подтверждение заново."
            if exc.status_code == 409
            else "Не удалось получить результат. Повторите подтверждение: дублей не возникнет."
        )
        await gateway_bot._reply(update, message)
        return
    finally:
        await client.aclose()
    if context.user_data is not None:
        context.user_data.pop(_EDITOR_REFERENCE_PENDING_KEY, None)
    await _editor_reply(
        update,
        f"✅ Подтверждено справочных материалов: {result.get('reviewedCount')}. "
        "Они сохранены отдельно от нормативной базы.",
        InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "← К документам", callback_data=f"editor:group:{pending['group']}:1"
                    )
                ]
            ]
        ),
    )


async def _show_group_approval(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, group: str
) -> None:
    actor = gateway_bot._actor_id(update)
    if actor is None or context.user_data is None:
        return
    client = LegalLibraryClient()
    try:
        preview = await client.get_group_preview(actor, group)
        snapshot = preview.get("snapshot")
        if not isinstance(snapshot, str) or re.fullmatch(r"[0-9a-f]{64}", snapshot) is None:
            raise ValueError("group snapshot")
        batch_id = str(uuid4())
        text, keyboard = render_group_approval_preview(preview, batch_id=batch_id, page=1)
        ids = [str(_editor_version_id(item.get("versionId"))) for item in preview["ready"]]
        context.user_data[_EDITOR_GROUP_PENDING_KEY] = {
            "id": batch_id,
            "group": group,
            "preview": preview,
            "ids": ids,
            "lastPageShown": len(ids) <= 10,
        }
    except (LegalCoreApiError, ValueError):
        await gateway_bot._reply(
            update, "Не удалось подготовить подтверждение. Откройте группу снова."
        )
        return
    finally:
        await client.aclose()
    await _editor_reply(update, text, keyboard)


async def _confirm_group_approval(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, batch_id: str
) -> None:
    actor = gateway_bot._actor_id(update)
    pending = (context.user_data or {}).get(_EDITOR_GROUP_PENDING_KEY)
    if (
        actor is None
        or not isinstance(pending, dict)
        or pending.get("id") != batch_id
        or not pending.get("lastPageShown")
        or not pending.get("ids")
    ):
        await gateway_bot._reply(update, "Откройте подтверждение группы и просмотрите список.")
        return
    client = LegalLibraryClient()
    try:
        result = await client.approve_group(
            actor,
            pending["group"],
            {
                "expectedSnapshot": pending["preview"]["snapshot"],
                "versionIds": pending["ids"],
                "officialTextCompared": True,
                "artifactIsComplete": True,
                "effectiveDatesVerified": True,
                "fragmentsVerified": True,
            },
            UUID(batch_id),
        )
    except LegalCoreApiError as exc:
        message = (
            "Список или состояние документов изменились. Откройте подтверждение группы заново."
            if exc.status_code == 409
            else "Не удалось получить результат. Повторите подтверждение: дублей не возникнет."
        )
        await gateway_bot._reply(update, message)
        return
    finally:
        await client.aclose()
    if context.user_data is not None:
        context.user_data.pop(_EDITOR_GROUP_PENDING_KEY, None)
    await _editor_reply(
        update,
        f"✅ Утверждено документов: {result.get('approvedCount')}. "
        "Неподготовленные материалы не утверждены.",
        InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "← К документам", callback_data=f"editor:group:{pending['group']}:1"
                    )
                ]
            ]
        ),
    )


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
            error_message = "🔒 Нормативная база доступна юристу и владельцу клиники."
        elif exc.code == "SUBSCRIPTION_INACTIVE":
            error_message = "🔒 Доступ клиники не активирован."
        else:
            error_message = "⚠️ Не удалось загрузить нормативную базу. Попробуйте позже."
        await gateway_bot._reply(update, error_message)
        return
    except ValueError:
        await gateway_bot._reply(
            update,
            "⚠️ Ответ нормативной базы некорректен.",
        )
        return
    finally:
        await client.aclose()

    effective_message = update.effective_message
    if effective_message is not None:
        await effective_message.reply_text(text, reply_markup=keyboard)


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
    install_reference_evaluations(application)
    application.add_handler(CommandHandler("legal_base", show_legal_library), group=-4)
    application.add_handler(CommandHandler("review_queue", show_platform_review_queue), group=-4)
    application.add_handler(
        CallbackQueryHandler(legal_editor_callback, pattern=_EDITOR_CALLBACK_RE),
        group=-4,
    )
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
