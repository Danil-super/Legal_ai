# ruff: noqa: RUF001
"""Telegram operator view for tenant clinic document library metadata."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
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

from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.clinic_document_runtime import (
    CLINIC_DOCUMENT_DATE_PENDING_KEY,
    PendingClinicDocumentUpload,
    arm_clinic_document_upload,
    build_application_with_clinic_documents,
)

logger = logging.getLogger(__name__)
_MAX_DOCUMENTS = 20
_MAX_VERSIONS = 15
_MAX_MESSAGE = 3900
_HISTORY_CALLBACK_RE = re.compile(
    r"^cliniclib:history:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
_LIBRARY_CALLBACK_RE = re.compile(
    r"^(clinicdocs:open|cliniclib:(open|upload|how|category:[a-z-]+|date:(today|manual|cancel)))$"
)
_DATE_PENDING_KEY = CLINIC_DOCUMENT_DATE_PENDING_KEY


@dataclass(frozen=True, slots=True)
class ClinicDocumentTemplate:
    key: str
    document_key: str
    document_type: str
    title: str
    button_text: str


_DOCUMENT_TEMPLATES: tuple[ClinicDocumentTemplate, ...] = (
    ClinicDocumentTemplate(
        "contract",
        "service-contract",
        "CONTRACT",
        "Договор на платные стоматологические услуги",
        "Договор на услуги",
    ),
    ClinicDocumentTemplate(
        "general-consent",
        "general-informed-consent",
        "INFORMED_CONSENT_GENERAL",
        "Общее информированное добровольное согласие",
        "Общее ИДС",
    ),
    ClinicDocumentTemplate(
        "implant-consent",
        "implant-informed-consent",
        "INFORMED_CONSENT_IMPLANT",
        "ИДС на имплантацию",
        "ИДС на имплантацию",
    ),
    ClinicDocumentTemplate(
        "warranty",
        "warranty-policy",
        "WARRANTY_POLICY",
        "Положение о гарантиях",
        "Гарантийное положение",
    ),
    ClinicDocumentTemplate(
        "claim-policy",
        "claim-policy",
        "CLAIM_POLICY",
        "Регламент работы с претензиями",
        "Регламент претензий",
    ),
    ClinicDocumentTemplate(
        "patient-rules",
        "patient-rules",
        "PATIENT_RULES",
        "Правила для пациентов",
        "Правила для пациентов",
    ),
    ClinicDocumentTemplate(
        "records-policy",
        "medical-record-access",
        "MEDICAL_RECORD_ACCESS_POLICY",
        "Порядок выдачи медицинских документов",
        "Выдача меддокументов",
    ),
    ClinicDocumentTemplate(
        "post-implant-memo",
        "post-implant-memo",
        "PATIENT_MEMO_POST_IMPLANT",
        "Памятка пациенту после имплантации",
        "Памятка после имплантации",
    ),
)
_TEMPLATES_BY_KEY = {item.key: item for item in _DOCUMENT_TEMPLATES}


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


def document_library_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📥 Добавить документ", callback_data="cliniclib:upload")],
            [InlineKeyboardButton("ℹ️ Как документы влияют на отчёт", callback_data="cliniclib:how")],
            [InlineKeyboardButton("← Главное меню", callback_data="menu")],
        ]
    )


def document_template_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                item.button_text,
                callback_data=f"cliniclib:category:{item.key}",
            )
        ]
        for item in _DOCUMENT_TEMPLATES
    ]
    rows.append([InlineKeyboardButton("✅ Пока достаточно", callback_data="cliniclib:open")])
    return InlineKeyboardMarkup(rows)


def document_effective_date_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Сегодня", callback_data="cliniclib:date:today"
                ),
                InlineKeyboardButton(
                    "Ввести дату", callback_data="cliniclib:date:manual"
                ),
            ],
            [InlineKeyboardButton("Отменить", callback_data="cliniclib:date:cancel")],
        ]
    )


def document_usage_help() -> str:
    return (
        "ℹ️ КАК ДОКУМЕНТЫ ВЛИЯЮТ НА ОТЧЁТ\n\n"
        "1. После загрузки файл безопасно разбирается, сохраняется как версия и ждёт одобрения.\n"
        "2. При анализе Legal Core берёт только одобренную версию вашей клиники, "
        "действующую на дату случая.\n"
        "3. Он ищет только релевантные фрагменты: например, гарантию — для случая с "
        "коронкой/имплантом, ИДС — для вопроса о согласии.\n"
        "4. Если документ использован, в отчёте будет раздел «Документы клиники» с "
        "названием, версией и разделом документа.\n\n"
        "Документы клиники — внутренний контекст, а не источник права: закон и проверенная "
        "нормативная база всегда имеют приоритет. Отсутствие документов не блокирует анализ, "
        "но бот покажет полезный checklist. Загружать весь список сразу не нужно: добавляйте "
        "только доступные и актуальные шаблоны, остальные можно загрузить позже.\n\n"
        "Если шаблон устарел, нажмите «Снять с использования» и подтвердите действие. Версия "
        "сразу исключается из новых анализов, но остаётся в истории; затем загрузите замену "
        "через «Добавить документ»."
    )


def render_library(payload: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup | None]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("items")
    if not raw_items:
        return (
            "📚 База документов клиники пока пуста.\n\n"
            "Это не блокирует создание кейсов, но одобренные шаблоны помогают точнее "
            "сформировать внутренний отчёт.",
            document_library_keyboard(),
        )

    lines = [
        "📚 Документы клиники",
        "",
        "Загрузка необязательна, но одобренные шаблоны могут сделать внутренний отчёт точнее.",
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
        if state == "PENDING":
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"✅ Одобрить {key}"[:60],
                        callback_data=f"clinicdoc:approve:{version_id}",
                    ),
                    InlineKeyboardButton(
                        f"⛔ Блок {key}"[:60],
                        callback_data=f"clinicdoc:block:{version_id}",
                    ),
                ]
            )
        elif state == "APPROVED":
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"🗄 Снять с использования {key}"[:60],
                        callback_data=f"clinicdoc:retire:{version_id}",
                    )
                ]
            )
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
    buttons.extend(document_library_keyboard().inline_keyboard)
    keyboard = InlineKeyboardMarkup(buttons)
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


async def clinic_document_library_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not isinstance(query.data, str):
        raise ApplicationHandlerStop
    match = _LIBRARY_CALLBACK_RE.fullmatch(query.data)
    if match is None:
        raise ApplicationHandlerStop
    await query.answer()
    action = "open" if match.group(1) == "clinicdocs:open" else match.group(2)
    if action is None:
        raise ApplicationHandlerStop
    if action == "open":
        await show_clinic_documents(update, context)
        raise ApplicationHandlerStop
    if action == "upload":
        await gateway_bot._reply(
            update,
            "📥 ВЫБЕРИТЕ ТИП ДОКУМЕНТА\n\n"
            "Выберите один подходящий шаблон — загружать все виды документов не требуется. "
            "После каждого шага можно вернуться в базу и продолжить позже. Если нужного вида здесь нет, пока не используйте "
            "техническую команду: сначала согласуем его тип, чтобы он корректно участвовал в "
            "отчёте. Загрузка необязательна.",
        )
        message = update.effective_message
        if message is not None:
            await message.reply_text("Доступные типы:", reply_markup=document_template_keyboard())
        raise ApplicationHandlerStop
    if action == "how":
        await gateway_bot._reply(update, document_usage_help())
        raise ApplicationHandlerStop

    if action == "date:cancel":
        if context.user_data is not None:
            context.user_data.pop(_DATE_PENDING_KEY, None)
        await gateway_bot._reply(update, "Выбор даты отменён. Документ не поставлен в очередь.")
        raise ApplicationHandlerStop
    if action == "date:today":
        pending = _date_pending(context)
        if pending is None:
            await gateway_bot._reply(update, "⚠️ Выбор документа истёк. Откройте базу и начните заново.")
            raise ApplicationHandlerStop
        _clear_date_pending(context)
        await arm_clinic_document_upload(
            update,
            context,
            PendingClinicDocumentUpload(
                document_key=pending.document_key,
                document_type=pending.document_type,
                title=pending.title,
                valid_from=date.today(),
            ),
        )
        raise ApplicationHandlerStop
    if action == "date:manual":
        if _date_pending(context) is None:
            await gateway_bot._reply(update, "⚠️ Выбор документа истёк. Откройте базу и начните заново.")
            raise ApplicationHandlerStop
        await gateway_bot._reply(
            update,
            "Введите дату начала действия версии в формате ГГГГ-ММ-ДД, например 2026-09-04. "
            "Для версии, которая ещё не действует, укажите будущую дату.",
        )
        raise ApplicationHandlerStop

    template_key = action.removeprefix("category:")
    template = _TEMPLATES_BY_KEY.get(template_key)
    if template is None:
        await gateway_bot._reply(update, "⚠️ Тип документа больше недоступен. Откройте базу заново.")
        raise ApplicationHandlerStop
    pending = PendingClinicDocumentUpload(
        document_key=template.document_key,
        document_type=template.document_type,
        title=template.title,
    )
    _set_date_pending(context, pending)
    await gateway_bot._reply(
        update,
        "📅 Укажите дату, с которой действует именно эта версия. "
        "Это нужно, чтобы документ не использовался в отчёте по более раннему кейсу.",
    )
    message = update.effective_message
    if message is not None:
        await message.reply_text("Дата начала действия:", reply_markup=document_effective_date_keyboard())
    raise ApplicationHandlerStop


def _date_pending(context: ContextTypes.DEFAULT_TYPE) -> PendingClinicDocumentUpload | None:
    data = context.user_data
    if not isinstance(data, dict):
        return None
    raw = data.get(_DATE_PENDING_KEY)
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


def _set_date_pending(
    context: ContextTypes.DEFAULT_TYPE,
    pending: PendingClinicDocumentUpload,
) -> None:
    if context.user_data is not None:
        context.user_data[_DATE_PENDING_KEY] = {
            "document_key": pending.document_key,
            "document_type": pending.document_type,
            "title": pending.title,
        }


def _clear_date_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data is not None:
        context.user_data.pop(_DATE_PENDING_KEY, None)


async def receive_document_effective_date(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    pending = _date_pending(context)
    message = update.effective_message
    if pending is None or message is None or not isinstance(message.text, str):
        return
    try:
        valid_from = date.fromisoformat(message.text.strip())
    except ValueError:
        await gateway_bot._reply(
            update,
            "Введите дату строго в формате ГГГГ-ММ-ДД, например 2026-09-04, или отмените выбор кнопкой.",
        )
        raise ApplicationHandlerStop
    _clear_date_pending(context)
    await arm_clinic_document_upload(
        update,
        context,
        PendingClinicDocumentUpload(
            document_key=pending.document_key,
            document_type=pending.document_type,
            title=pending.title,
            valid_from=valid_from,
        ),
    )
    raise ApplicationHandlerStop


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
            clinic_document_library_callback,
            pattern=(
                r"^(clinicdocs:open|cliniclib:(open|upload|how|category:[a-z-]+|"
                r"date:(today|manual|cancel)))$"
            ),
        ),
        group=-2,
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_document_effective_date),
        group=-2,
    )
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
