# ruff: noqa: RUF001
"""Explicit Telegram workflow for tenant-owned clinic reference documents.

Arbitrary Telegram attachments are never ingested. An administrator must first start an upload
command, then send exactly one bounded TXT/PDF/DOCX file, and finally approve or block the created
version with a separate callback. Legal Core remains authoritative for tenant access and approval.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

import httpx2
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_gateway import analysis_runtime
from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError

logger = logging.getLogger(__name__)

_PENDING_KEY = "clinic_document_upload"
_MAX_UPLOAD_BYTES = 15_000_000
_DOCUMENT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")
_DOCUMENT_TYPE_RE = re.compile(r"^[A-Z0-9_]{3,80}$")
_REVIEW_CALLBACK_RE = re.compile(
    r"^clinicdoc:(approve|block):"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_EXTENSION_MIME = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": _DOCX_MIME,
}


@dataclass(frozen=True, slots=True)
class PendingClinicDocumentUpload:
    document_key: str
    document_type: str
    title: str


def parse_upload_command(arguments: list[str]) -> PendingClinicDocumentUpload:
    if len(arguments) < 3:
        raise ValueError("usage")
    document_key = arguments[0].strip().casefold()
    document_type = arguments[1].strip().upper()
    title = " ".join(arguments[2:]).strip()
    if _DOCUMENT_KEY_RE.fullmatch(document_key) is None:
        raise ValueError("document_key")
    if _DOCUMENT_TYPE_RE.fullmatch(document_type) is None:
        raise ValueError("document_type")
    if not 1 <= len(title) <= 240:
        raise ValueError("title")
    return PendingClinicDocumentUpload(
        document_key=document_key,
        document_type=document_type,
        title=title,
    )


def review_keyboard(version_id: UUID) -> InlineKeyboardMarkup:
    approve = f"clinicdoc:approve:{version_id}"
    block = f"clinicdoc:block:{version_id}"
    if len(approve.encode()) > 64 or len(block.encode()) > 64:
        raise ValueError("clinic document callback data is too long")
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Одобрить", callback_data=approve),
                InlineKeyboardButton("⛔ Заблокировать", callback_data=block),
            ]
        ]
    )


def _pending(context: ContextTypes.DEFAULT_TYPE) -> PendingClinicDocumentUpload | None:
    data = context.user_data
    if not isinstance(data, dict):
        return None
    raw = data.get(_PENDING_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return PendingClinicDocumentUpload(
            document_key=str(raw["document_key"]),
            document_type=str(raw["document_type"]),
            title=str(raw["title"]),
        )
    except KeyError:
        return None


def _set_pending(
    context: ContextTypes.DEFAULT_TYPE,
    pending: PendingClinicDocumentUpload,
) -> None:
    if context.user_data is None:
        return
    context.user_data[_PENDING_KEY] = {
        "document_key": pending.document_key,
        "document_type": pending.document_type,
        "title": pending.title,
    }


def _clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data is not None:
        context.user_data.pop(_PENDING_KEY, None)


def _actor_id(update: Update) -> int | None:
    user = update.effective_user
    return None if user is None else user.id


class ClinicDocumentCoreClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._http = client or httpx2.AsyncClient(
            base_url=base_url or gateway_bot.load_legal_core_url(),
            timeout=30.0,
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    @staticmethod
    def _error_code(response: httpx2.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return "LEGAL_CORE_ERROR"
        if not isinstance(payload, dict):
            return "LEGAL_CORE_ERROR"
        error = payload.get("error")
        if not isinstance(error, dict):
            return "LEGAL_CORE_ERROR"
        code = error.get("code")
        return code if isinstance(code, str) and code else "LEGAL_CORE_ERROR"

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        telegram_user_id: int,
        json_body: dict[str, Any] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
        source_filename: str | None = None,
    ) -> dict[str, Any]:
        headers = {"X-Telegram-User-Id": str(telegram_user_id)}
        if content_type is not None:
            headers["Content-Type"] = content_type
        if source_filename is not None:
            headers["X-Source-Filename"] = source_filename
        try:
            response = await self._http.request(
                method,
                path,
                headers=headers,
                json=json_body,
                content=content,
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
        if response.status_code >= 400:
            raise LegalCoreApiError(
                response.status_code,
                self._error_code(response),
                "Legal Core rejected clinic document request",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise LegalCoreApiError(
                502,
                "INVALID_LEGAL_CORE_RESPONSE",
                "Invalid Legal Core response",
            ) from exc
        if not isinstance(payload, dict):
            raise LegalCoreApiError(
                502,
                "INVALID_LEGAL_CORE_RESPONSE",
                "Invalid Legal Core response",
            )
        return payload

    async def get_actor(self, telegram_user_id: int) -> dict[str, Any]:
        return await self._request_json(
            "GET",
            "/v1/actor",
            telegram_user_id=telegram_user_id,
        )

    async def create_document(
        self,
        *,
        telegram_user_id: int,
        pending: PendingClinicDocumentUpload,
    ) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            "/v1/clinic-documents",
            telegram_user_id=telegram_user_id,
            json_body={
                "documentKey": pending.document_key,
                "documentType": pending.document_type,
                "title": pending.title,
            },
        )

    async def upload_file_version(
        self,
        *,
        telegram_user_id: int,
        document_id: UUID,
        source_filename: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            f"/v1/clinic-documents/{document_id}/file-versions",
            telegram_user_id=telegram_user_id,
            content=content,
            content_type=content_type,
            source_filename=source_filename,
        )

    async def review_version(
        self,
        *,
        telegram_user_id: int,
        version_id: UUID,
        decision: str,
        reason_code: str,
    ) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            f"/v1/clinic-documents/versions/{version_id}/approval-events",
            telegram_user_id=telegram_user_id,
            json_body={"decision": decision, "reasonCode": reason_code},
        )


def _friendly_error(error: LegalCoreApiError) -> str:
    messages = {
        "ACCESS_DENIED": "Доступ администратора не подтверждён.",
        "CLINIC_DOCUMENT_KEY_CONFLICT": (
            "Такой ключ документа уже занят другими метаданными. Используйте другой ключ."
        ),
        "CLINIC_DOCUMENT_FILE_TOO_LARGE": "Файл больше допустимого лимита 15 МБ.",
        "CLINIC_DOCUMENT_FILE_INVALID": (
            "Файл не прошёл безопасный локальный разбор. Проверьте формат и содержимое."
        ),
        "CLINIC_DOCUMENT_VERSION_METADATA_CONFLICT": (
            "Эти же байты уже зарегистрированы с другими метаданными версии."
        ),
        "CLINIC_DOCUMENT_REPROCESSING_CONFLICT": (
            "Повторная обработка файла дала другой результат. Требуется проверка."
        ),
        "CLINIC_DOCUMENT_STORAGE_UNAVAILABLE": "Хранилище документов временно недоступно.",
        "CLINIC_DOCUMENT_STORAGE_NOT_CONFIGURED": "Хранилище документов ещё не настроено.",
        "CLINIC_DOCUMENT_PARSER_UNAVAILABLE": "Парсер документов временно недоступен.",
    }
    if error.status_code == 403:
        return "Доступ администратора отозван или не активирован."
    if error.status_code == 404:
        return "Документ или версия не найдены в вашей клинике."
    return messages.get(error.code, "Не удалось безопасно обработать документ.")


async def start_clinic_document_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    actor_id = _actor_id(update)
    if actor_id is None:
        await gateway_bot._reply(update, "Не удалось определить администратора.")
        return
    if gateway_bot.WIZARD_DATA_KEY in (context.user_data or {}):
        await gateway_bot._reply(
            update,
            "Сначала завершите или отмените заполнение текущего кейса.",
        )
        return
    try:
        pending = parse_upload_command(list(context.args or []))
    except ValueError:
        await gateway_bot._reply(
            update,
            "Использование:\n"
            "/upload_clinic_doc <key> <TYPE> <название>\n\n"
            "Пример:\n"
            "/upload_clinic_doc warranty-main WARRANTY_POLICY Гарантийное положение",
        )
        return

    core = ClinicDocumentCoreClient()
    try:
        await core.get_actor(actor_id)
    except LegalCoreApiError as exc:
        logger.warning("clinic document upload authorization failed: %s", exc.code)
        await gateway_bot._reply(update, f"⚠️ {_friendly_error(exc)}")
        return
    finally:
        await core.aclose()

    _set_pending(context, pending)
    await gateway_bot._reply(
        update,
        "📄 Режим загрузки документа включён.\n\n"
        f"Ключ: {pending.document_key}\n"
        f"Тип: {pending.document_type}\n"
        f"Название: {pending.title}\n\n"
        "Теперь отправьте ОДИН файл .txt, .pdf или .docx до 15 МБ.\n"
        "Важно: загружайте шаблон/политику клиники, а не документы конкретного пациента. "
        "Не отправляйте ФИО, телефон, медицинскую карту или другие персональные данные.\n\n"
        "После загрузки файл НЕ попадёт в анализ автоматически — потребуется отдельное одобрение.",
    )


async def cancel_clinic_document_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    _clear_pending(context)
    await gateway_bot._reply(update, "Загрузка документа отменена.")


async def receive_clinic_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    actor_id = _actor_id(update)
    pending = _pending(context)
    if message is None or actor_id is None:
        raise ApplicationHandlerStop
    if pending is None:
        await gateway_bot._reply(
            update,
            "Файл не загружен. Сначала явно включите режим командой /upload_clinic_doc.",
        )
        raise ApplicationHandlerStop
    if gateway_bot.WIZARD_DATA_KEY in (context.user_data or {}):
        await gateway_bot._reply(
            update,
            "Во время заполнения кейса документы клиники не загружаются. Завершите кейс или /cancel.",
        )
        raise ApplicationHandlerStop

    document = message.document
    if document is None or not document.file_name:
        await gateway_bot._reply(update, "Не удалось определить имя файла.")
        raise ApplicationHandlerStop
    file_size = document.file_size
    if isinstance(file_size, int) and file_size > _MAX_UPLOAD_BYTES:
        await gateway_bot._reply(update, "Файл больше допустимого лимита 15 МБ.")
        raise ApplicationHandlerStop

    source_filename = document.file_name.strip()
    extension = PurePosixPath(source_filename).suffix.casefold()
    expected_mime = _EXTENSION_MIME.get(extension)
    if expected_mime is None:
        await gateway_bot._reply(update, "Поддерживаются только .txt, .pdf и .docx.")
        raise ApplicationHandlerStop
    content_type = (document.mime_type or "application/octet-stream").split(";", 1)[0]
    if content_type not in {expected_mime, "application/octet-stream"}:
        await gateway_bot._reply(update, "Расширение файла не совпадает с его MIME-типом.")
        raise ApplicationHandlerStop

    await gateway_bot._reply(update, "Проверяю файл и сохраняю его как новую версию…")
    try:
        telegram_file = await context.bot.get_file(document.file_id)
        downloaded = await telegram_file.download_as_bytearray()
        raw = bytes(downloaded)
        if not raw or len(raw) > _MAX_UPLOAD_BYTES:
            raise ValueError("download size")

        core = ClinicDocumentCoreClient()
        try:
            created = await core.create_document(
                telegram_user_id=actor_id,
                pending=pending,
            )
            document_id = UUID(str(created["id"]))
            version = await core.upload_file_version(
                telegram_user_id=actor_id,
                document_id=document_id,
                source_filename=source_filename,
                content_type=content_type,
                content=raw,
            )
        finally:
            await core.aclose()

        version_id = UUID(str(version["id"]))
        version_no = int(version["versionNo"])
        raw_sha = str(version["rawSha256"])
        text_sha = str(version["normalizedTextSha256"])
        fragment_count = int(version["fragmentCount"])
    except LegalCoreApiError as exc:
        logger.warning("clinic document upload failed: %s", exc.code)
        await gateway_bot._reply(update, f"⚠️ {_friendly_error(exc)}")
        raise ApplicationHandlerStop
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("clinic document upload response invalid: %s", type(exc).__name__)
        await gateway_bot._reply(update, "⚠️ Документ не прошёл внутреннюю проверку ответа.")
        raise ApplicationHandlerStop

    _clear_pending(context)
    await message.reply_text(
        "✅ Версия сохранена, но пока НЕ одобрена.\n\n"
        f"Версия: {version_no}\n"
        f"Фрагментов: {fragment_count}\n"
        f"Raw SHA-256: {raw_sha[:16]}…\n"
        f"Text SHA-256: {text_sha[:16]}…\n\n"
        "Одобрение означает, что документ можно использовать как внутренний контекст клиники. "
        "Он НЕ становится нормативным источником и не может подтверждать юридическую норму.",
        reply_markup=review_keyboard(version_id),
    )
    raise ApplicationHandlerStop


async def review_clinic_document_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    actor_id = _actor_id(update)
    if query is None or actor_id is None or not isinstance(query.data, str):
        raise ApplicationHandlerStop
    match = _REVIEW_CALLBACK_RE.fullmatch(query.data)
    if match is None:
        raise ApplicationHandlerStop
    await query.answer()
    action, version_text = match.groups()
    version_id = UUID(version_text)
    if action == "approve":
        decision = "APPROVED"
        reason_code = "CLINIC_REVIEW_PASSED"
    else:
        decision = "BLOCKED"
        reason_code = "CLINIC_DOCUMENT_REVOKED"

    core = ClinicDocumentCoreClient()
    try:
        result = await core.review_version(
            telegram_user_id=actor_id,
            version_id=version_id,
            decision=decision,
            reason_code=reason_code,
        )
        if result.get("decision") != decision:
            raise ValueError("unexpected review decision")
    except LegalCoreApiError as exc:
        logger.warning("clinic document review failed: %s", exc.code)
        await gateway_bot._reply(update, f"⚠️ {_friendly_error(exc)}")
        raise ApplicationHandlerStop
    except ValueError:
        await gateway_bot._reply(update, "⚠️ Ответ проверки документа некорректен.")
        raise ApplicationHandlerStop
    finally:
        await core.aclose()

    if decision == "APPROVED":
        text = (
            "✅ Документ одобрен. Теперь его актуальная версия может использоваться "
            "как внутренний контекст вашей клиники. Нормативной правовой базой он не является."
        )
    else:
        text = "⛔ Версия заблокирована и не будет попадать в контекст анализа."
    try:
        await query.edit_message_text(text=text)
    except Exception:  # pragma: no cover - fallback is safe and operator-visible.
        logger.exception("clinic document review message edit failed")
        await gateway_bot._reply(update, text)
    raise ApplicationHandlerStop


def build_application_with_clinic_documents(token: str) -> gateway_bot.TelegramApplication:
    application = analysis_runtime.build_application_with_analysis(token)
    application.add_handler(
        CommandHandler("upload_clinic_doc", start_clinic_document_upload),
        group=-2,
    )
    application.add_handler(
        CommandHandler("cancel_upload", cancel_clinic_document_upload),
        group=-2,
    )
    application.add_handler(
        MessageHandler(filters.Document.ALL, receive_clinic_document),
        group=-2,
    )
    application.add_handler(
        CallbackQueryHandler(
            review_clinic_document_callback,
            pattern=(
                r"^clinicdoc:(approve|block):"
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
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

    application = build_application_with_clinic_documents(gateway_bot.load_token())
    application.run_polling(
        allowed_updates=gateway_bot.ALLOWED_UPDATES,
        bootstrap_retries=3,
        drop_pending_updates=False,
    )
