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
from telegram_gateway.quick_intake_runtime import build_application_with_quick_intake
from telegram_gateway.ui import back_keyboard

logger = logging.getLogger(__name__)
LEGAL_LIBRARY_CALLBACK = "legalbase:open"
_MAX_DOCUMENTS = 20
_MAX_MESSAGE = 3_900
_EDITOR_PENDING_KEY = "legal_editor_pending"
_EDITOR_ARTIFACT_MAX_BYTES = 50_000_000
_EDITOR_CALLBACK_RE = re.compile(
    r"^editor:(?:open|page:(?:[1-9]|[1-9][0-9]|100)|"
    r"detail:[0-9a-f-]{36}:(?:[1-9]|[1-9][0-9]|100)|"
    r"artifact:[0-9a-f-]{36}|"
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
        try:
            async with self._http.stream(
                "GET",
                f"/v1/legal/review-queue/{version_id}/artifact",
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
                if mime_type not in {"application/pdf", "text/plain"}:
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
                "  "
                + ("можно проверить и подтвердить" if eligible else "старая/недоступная версия"),
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
    buttons.extend([list(row) for row in back_keyboard().inline_keyboard])
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
            "Источник: КонсультантПлюс — проверенная копия, не первичная публикация."
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
                    "🌐 Источник: КонсультантПлюс"
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
                    "📄 Открыть PDF", callback_data=f"editor:artifact:{version_id}"
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
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None:
        return
    client = LegalLibraryClient()
    try:
        text, keyboard = render_platform_review_queue(
            await client.get_review_queue(actor_id, page=page)
        )
    except LegalCoreApiError as exc:
        logger.warning("legal review queue load failed: %s", exc.code)
        await gateway_bot._reply(
            update, "🔒 Проверка норм доступна только уполномоченному редактору."
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
        pending = _new_editor_state(detail) if reset else _pending_editor_state(context)
        if pending is None or pending.get("versionId") != str(version_id):
            pending = _new_editor_state(detail)
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


async def _send_editor_artifact(update: Update, *, version_id: UUID) -> None:
    actor_id = gateway_bot._actor_id(update)
    if actor_id is None or update.effective_message is None:
        return
    client = LegalLibraryClient()
    try:
        content, mime_type = await client.download_editor_artifact(actor_id, version_id)
        filename = f"legal-{version_id}{'.pdf' if mime_type == 'application/pdf' else '.txt'}"
        await update.effective_message.reply_document(
            document=InputFile(BytesIO(content), filename=filename),
            caption="Сохранённая неизменяемая версия документа для юридической проверки.",
        )
    except LegalCoreApiError as exc:
        logger.warning("legal editor artifact failed: %s", exc.code)
        await gateway_bot._reply(update, "⚠️ Документ не удалось загрузить. Попробуйте позже.")
    finally:
        await client.aclose()


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
        elif callback_data.startswith("editor:detail:"):
            _, _, raw_version_id, _ = callback_data.split(":")
            await _show_editor_detail(update, context, version_id=UUID(raw_version_id), reset=True)
        elif callback_data.startswith("editor:artifact:"):
            await _send_editor_artifact(update, version_id=UUID(callback_data.rsplit(":", 1)[1]))
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
