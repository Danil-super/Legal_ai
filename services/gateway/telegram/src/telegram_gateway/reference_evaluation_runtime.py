# ruff: noqa: RUF001
"""Private Telegram workspace for reviewed, de-identified reference cases.

This is deliberately separate from the ordinary patient-case wizard and legal
library.  The workspace only collects de-identified historical examples for a
future evaluation runner; it never changes legal sources, prompts, risk policy,
or sends an answer to a patient.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, cast
from uuid import UUID, uuid4

from legal_core.pseudonymization import pseudonymize_text
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError, LegalCoreClient
from telegram_gateway.quick_intake import contains_probable_person_name
from telegram_gateway.ui import back_keyboard

logger = logging.getLogger(__name__)

PENDING_KEY = "reference_evaluation_pending"
_MAX_UPLOAD_BYTES = 15_000_000
_MAX_SCENARIO_CHARS = 20_000
_GROUPS = {
    "clinical": "Клинические справочные материалы",
    "labour": "Труд и квалификация специалистов",
    "courts": "Суды, экспертиза и юридическая помощь",
    "privacy": "Персональные данные и информация",
    "licensing": "Лицензирование и контроль",
    "healthcare": "Медицинская деятельность и права пациентов",
    "general": "Кодексы и общие правовые нормы",
}
_ROUTES = {
    "ABSTAIN": "Безопасный отказ: данных/оснований недостаточно",
    "HUMAN_ESCALATION": "Передать юристу для уточнения",
    "INTERNAL_DRAFT": "Внутренний черновик с источниками",
}
_STATUS = {
    "DRAFT": "Черновик",
    "READY_FOR_REVIEW": "Ожидает проверки",
    "CHANGES_REQUIRED": "Нужны доработки",
    "APPROVED_FOR_EVALUATION": "Одобрен для будущей оценки",
    "REJECTED": "Не принят для оценки",
    "RETIRED": "Снят с оценки",
}
_EXTENSION_MIME = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_CALLBACK_RE = re.compile(
    r"^refeval:(?:open|new|confirm|attachment:(?:skip|submit)|"
    r"group:(?:clinical|labour|courts|privacy|licensing|healthcare|general)|"
    r"route:(?:ABSTAIN|HUMAN_ESCALATION|INTERNAL_DRAFT)|"
    r"list:[0-9a-f-]{36}|view:[0-9a-f-]{36}|download:[0-9a-f-]{36}|"
    r"approve:[0-9a-f-]{36}|reject:[0-9a-f-]{36}|changes:[0-9a-f-]{36}|"
    r"revise:[0-9a-f-]{36})$"
)


def _actor_id(update: Update) -> int | None:
    return None if update.effective_user is None else update.effective_user.id


def _user_data(context: ContextTypes.DEFAULT_TYPE) -> dict[Any, Any]:
    if context.user_data is None:
        raise RuntimeError("Telegram user storage is unavailable")
    return context.user_data


def _core(context: ContextTypes.DEFAULT_TYPE) -> LegalCoreClient:
    client = context.bot_data.get(gateway_bot.LEGAL_CORE_CLIENT_KEY)
    if client is None:
        raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "Legal Core unavailable")
    return cast(LegalCoreClient, client)


def _clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    _user_data(context).pop(PENDING_KEY, None)


def _pending(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    value = _user_data(context).get(PENDING_KEY)
    return value if isinstance(value, dict) else None


def _set_pending(context: ContextTypes.DEFAULT_TYPE, value: dict[str, Any]) -> None:
    _user_data(context)[PENDING_KEY] = value


def _stable_action_key(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    case_id: UUID,
    current_version: int,
    action: str,
) -> UUID:
    """Retain a callback action key so Telegram redelivery cannot create a second review."""

    data = _user_data(context)
    keys = data.setdefault("reference_evaluation_action_keys", {})
    if not isinstance(keys, dict):
        keys = {}
        data["reference_evaluation_action_keys"] = keys
    value = keys.setdefault(f"{case_id}:{current_version}:{action}", str(uuid4()))
    try:
        return UUID(str(value))
    except ValueError:
        refreshed = uuid4()
        keys[f"{case_id}:{current_version}:{action}"] = str(refreshed)
        return refreshed


def _case_callback(action: str, case_id: UUID) -> str:
    value = f"refeval:{action}:{case_id}"
    if len(value.encode()) > 64:
        raise ValueError("reference evaluation callback is too long")
    return value


def _keyboards(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([*rows, *back_keyboard().inline_keyboard])


def _groups_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"refeval:group:{key}")]
        for key, label in _GROUPS.items()
    ]
    return _keyboards(rows)


def _routes_keyboard() -> InlineKeyboardMarkup:
    return _keyboards(
        [
            [InlineKeyboardButton(label, callback_data=f"refeval:route:{key}")]
            for key, label in _ROUTES.items()
        ]
    )


def _date_keyboard() -> InlineKeyboardMarkup:
    return _keyboards([])


def _summary_text(pending: dict[str, Any]) -> str:
    group = _GROUPS.get(str(pending.get("groupKey")), "не выбрана")
    route = _ROUTES.get(str(pending.get("expectedRoute")), "не выбран")
    scenario = str(pending.get("scenarioText", "")).strip()
    return (
        "📋 ПРОВЕРКА ЭТАЛОННОГО КЕЙСА\n\n"
        f"Группа покрытия: {group}\n"
        f"Дата оценки: {pending.get('asOfDate', 'не указана')}\n"
        f"Ожидаемая безопасная обработка: {route}\n\n"
        f"Фабула (обезличена):\n{scenario}\n\n"
        "Это не юридическое заключение и не источник права. Проверьте, что здесь нет "
        "ФИО, контактов, номеров документов или иных данных пациента."
    )


def _summary_keyboard() -> InlineKeyboardMarkup:
    return _keyboards(
        [
            [
                InlineKeyboardButton(
                    "✅ Сохранить и перейти к материалу", callback_data="refeval:confirm"
                )
            ]
        ]
    )


def _attachment_keyboard() -> InlineKeyboardMarkup:
    return _keyboards(
        [
            [
                InlineKeyboardButton(
                    "📎 Прикрепить обезличенный файл", callback_data="refeval:attachment:skip"
                )
            ],
            [
                InlineKeyboardButton(
                    "➡️ Отправить юристу без файла", callback_data="refeval:attachment:submit"
                )
            ],
        ]
    )


def _material_attached_keyboard() -> InlineKeyboardMarkup:
    return _keyboards(
        [
            [
                InlineKeyboardButton(
                    "➡️ Отправить юристу на проверку", callback_data="refeval:attachment:submit"
                )
            ]
        ]
    )


def _review_keyboard(
    case_id: UUID,
    *,
    can_review: bool,
    created_by_self: bool,
    status: str,
    has_material: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_material:
        rows.append(
            [
                InlineKeyboardButton(
                    "📎 Скачать обезличенный материал",
                    callback_data=_case_callback("download", case_id),
                )
            ]
        )
    if status == "READY_FOR_REVIEW" and can_review and not created_by_self:
        rows.append(
            [
                InlineKeyboardButton(
                    "✅ Одобрить для оценки", callback_data=_case_callback("approve", case_id)
                ),
                InlineKeyboardButton(
                    "⛔ Не принять", callback_data=_case_callback("reject", case_id)
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    "✏️ Запросить доработку", callback_data=_case_callback("changes", case_id)
                )
            ]
        )
    if status == "CHANGES_REQUIRED" and created_by_self:
        rows.append(
            [
                InlineKeyboardButton(
                    "✏️ Подготовить новую версию", callback_data=_case_callback("revise", case_id)
                )
            ]
        )
    rows.append([InlineKeyboardButton("← К списку", callback_data="refeval:open")])
    return _keyboards(rows)


def _friendly_error(error: LegalCoreApiError) -> str:
    if error.status_code == 403:
        return "🔒 Рабочее место эталонных кейсов недоступно."
    if error.code == "REFERENCE_EVALUATION_DIRECT_IDENTIFIER_NOT_ALLOWED":
        return "⚠️ Обнаружены персональные данные. Удалите их и повторите действие."
    if error.code in {
        "REFERENCE_EVALUATION_FILE_INVALID",
        "REFERENCE_EVALUATION_MATERIAL_UPLOAD_REJECTED",
    }:
        return "⚠️ Файл не прошёл проверку. Поддерживаются обезличенные TXT, PDF и DOCX до 15 МБ."
    if error.code in {
        "REFERENCE_EVALUATION_STORAGE_UNAVAILABLE",
        "REFERENCE_EVALUATION_STORAGE_NOT_CONFIGURED",
    }:
        return "⚠️ Защищённое хранилище временно недоступно. Повторите позже."
    if error.status_code == 409:
        return "⚠️ Состояние кейса уже изменилось. Откройте список и проверьте карточку."
    return "⚠️ Не удалось безопасно выполнить действие. Повторите позже."


async def _access(client: LegalCoreClient, actor_id: int) -> dict[str, bool]:
    result = await client.get_reference_evaluation_access(actor_id)
    return {
        "canContribute": result.get("canContribute") is True,
        "canReview": result.get("canReview") is True,
    }


async def _show_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    before: UUID | None = None,
) -> None:
    actor_id = _actor_id(update)
    if actor_id is None:
        await gateway_bot._reply(update, "Не удалось определить пользователя.")
        return
    try:
        client = _core(context)
        access, payload = await asyncio.gather(
            _access(client, actor_id),
            client.list_reference_evaluations(actor_id, before=before, limit=12),
        )
    except LegalCoreApiError as exc:
        logger.warning("reference evaluation list unavailable: %s", exc.code)
        await gateway_bot._reply(update, _friendly_error(exc))
        return
    if not access["canContribute"] and not access["canReview"]:
        await gateway_bot._reply(update, "🔒 Рабочее место эталонных кейсов недоступно.")
        return
    items = payload.get("items")
    if not isinstance(items, list):
        await gateway_bot._reply(update, "⚠️ Получен некорректный ответ рабочего места.")
        return
    rows: list[list[InlineKeyboardButton]] = []
    if access["canContribute"]:
        rows.append(
            [InlineKeyboardButton("➕ Добавить эталонный кейс", callback_data="refeval:new")]
        )
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        name = item.get("displayName")
        state = item.get("status")
        try:
            case_id = UUID(str(raw_id))
        except (TypeError, ValueError):
            continue
        if not isinstance(name, str) or not isinstance(state, str):
            continue
        label = _STATUS.get(state, "Неизвестный статус")
        rows.append(
            [
                InlineKeyboardButton(
                    f"📁 {name[:42]} · {label}"[:64], callback_data=_case_callback("view", case_id)
                )
            ]
        )
    next_before = payload.get("nextBefore")
    if isinstance(next_before, str):
        try:
            rows.append(
                [
                    InlineKeyboardButton(
                        "Вперёд ▶️", callback_data=_case_callback("list", UUID(next_before))
                    )
                ]
            )
        except ValueError:
            logger.warning("reference evaluation list returned invalid cursor")
    body = (
        "🗂 ЭТАЛОННЫЕ КЕЙСЫ\n\n"
        "Обезличенные примеры для проверки бота. Откройте кейс для просмотра "
        "фабулы, кандидата ответа и полного материала.\n\n"
        "Черновики не являются проверенными ответами или источниками права."
    )
    if not items:
        body += "\n\nСписок пока пуст."
    await gateway_bot._reply(update, body, reply_markup=_keyboards(rows))


async def show_reference_evaluations(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    _clear_pending(context)
    await _show_list(update, context)


async def _begin_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor_id = _actor_id(update)
    if actor_id is None:
        await gateway_bot._reply(update, "Не удалось определить пользователя.")
        return
    try:
        access = await _access(_core(context), actor_id)
    except LegalCoreApiError as exc:
        await gateway_bot._reply(update, _friendly_error(exc))
        return
    if not access["canContribute"]:
        await gateway_bot._reply(update, "🔒 Добавление эталонных кейсов вам недоступно.")
        return
    gateway_bot._clear_pending_inputs(context)
    _set_pending(context, {"mode": "create", "stage": "group"})
    await gateway_bot._reply(
        update,
        "➕ НОВЫЙ ЭТАЛОННЫЙ КЕЙС\n\n"
        "Добавляйте только обезличенный исторический пример. Не отправляйте ФИО, телефоны, "
        "адреса, даты рождения, номера карт/документов, фото или неотредактированные "
        "меддокументы.\n\n"
        "Выберите группу покрытия. Это не юридическая квалификация ситуации.",
        reply_markup=_groups_keyboard(),
    )


async def _begin_revision(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    case_id: UUID,
) -> None:
    actor_id = _actor_id(update)
    if actor_id is None:
        return
    try:
        detail = await _core(context).get_reference_evaluation(case_id, actor_id)
    except LegalCoreApiError as exc:
        await gateway_bot._reply(update, _friendly_error(exc))
        return
    if detail.get("status") != "CHANGES_REQUIRED" or detail.get("createdBySelf") is not True:
        await gateway_bot._reply(
            update, "⚠️ Новую версию может подготовить только автор после замечаний."
        )
        return
    gateway_bot._clear_pending_inputs(context)
    _set_pending(context, {"mode": "revision", "caseId": str(case_id), "stage": "date"})
    await gateway_bot._reply(
        update,
        "✏️ НОВАЯ ВЕРСИЯ\n\nУкажите дату, на которую проверяется пример, в формате ГГГГ-ММ-ДД.",
        reply_markup=_date_keyboard(),
    )


async def _show_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    case_id: UUID,
) -> None:
    actor_id = _actor_id(update)
    if actor_id is None:
        return
    try:
        client = _core(context)
        access, detail = await asyncio.gather(
            _access(client, actor_id), client.get_reference_evaluation(case_id, actor_id)
        )
    except LegalCoreApiError as exc:
        logger.warning("reference evaluation detail unavailable: %s", exc.code)
        await gateway_bot._reply(update, _friendly_error(exc))
        return
    group = _GROUPS.get(str(detail.get("groupKey")), "неизвестна")
    route = _ROUTES.get(str(detail.get("expectedRoute")), "не выбран")
    state = _STATUS.get(str(detail.get("status")), "Неизвестный статус")
    scenario = detail.get("scenarioText")
    scenario_label = (
        scenario if isinstance(scenario, str) else "Содержимое удалено по сроку хранения."
    )
    body = (
        f"📁 {detail.get('displayName', 'Эталонный кейс')}\n"
        f"Статус: {state}\n"
        f"Группа покрытия: {group}\n"
        f"Дата оценки: {detail.get('asOfDate', '—')}\n"
        f"Ожидаемая обработка: {route}\n\n"
        f"Фабула:\n{scenario_label}\n\n"
        "Статус кейса не одобряет правовую норму и не включает его в ответы пользователям."
    )
    await gateway_bot._reply(
        update,
        body[:3900],
        reply_markup=_review_keyboard(
            case_id,
            can_review=access["canReview"],
            created_by_self=detail.get("createdBySelf") is True,
            status=str(detail.get("status")),
            has_material=detail.get("hasMaterial") is True,
        ),
    )


def _parse_safe_date(value: str) -> str | None:
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.isoformat() if parsed <= datetime.now(UTC).date() else None


async def _receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = _pending(context)
    message = update.effective_message
    if pending is None or message is None or not isinstance(message.text, str):
        return
    stage = pending.get("stage")
    if stage not in {"date", "scenario", "review_note"}:
        return
    actor_id = _actor_id(update)
    if actor_id is None:
        raise ApplicationHandlerStop
    value = message.text.strip()
    if stage == "date":
        parsed = _parse_safe_date(value)
        if parsed is None:
            await gateway_bot._reply(
                update, "Введите дату не позднее сегодняшней в формате ГГГГ-ММ-ДД."
            )
            raise ApplicationHandlerStop
        pending["asOfDate"] = parsed
        pending["stage"] = "scenario" if pending.get("expectedRoute") else "route"
        if pending["stage"] == "route":
            await gateway_bot._reply(
                update,
                "Выберите ожидаемую безопасную обработку. Это проверочная метка, "
                "не решение по пациенту.",
                reply_markup=_routes_keyboard(),
            )
        else:
            await gateway_bot._reply(
                update,
                "Опишите фабулу одним сообщением (10–20 000 символов) без персональных данных.",
                reply_markup=_date_keyboard(),
            )
        raise ApplicationHandlerStop
    if stage == "scenario":
        if not 10 <= len(value) <= _MAX_SCENARIO_CHARS:
            await gateway_bot._reply(update, "Фабула должна содержать от 10 до 20 000 символов.")
            raise ApplicationHandlerStop
        if pseudonymize_text(value).changed or contains_probable_person_name(value):
            await gateway_bot._reply(
                update,
                "⚠️ В тексте обнаружены возможные персональные данные. Удалите их и отправьте "
                "обезличенную фабулу ещё раз.",
            )
            raise ApplicationHandlerStop
        pending["scenarioText"] = value
        pending["stage"] = "confirm"
        await gateway_bot._reply(update, _summary_text(pending), reply_markup=_summary_keyboard())
        raise ApplicationHandlerStop
    if not 2 <= len(value) <= 1_000:
        await gateway_bot._reply(update, "Комментарий должен содержать от 2 до 1 000 символов.")
        raise ApplicationHandlerStop
    if pseudonymize_text(value).changed or contains_probable_person_name(value):
        await gateway_bot._reply(
            update, "⚠️ Удалите персональные данные из комментария и повторите."
        )
        raise ApplicationHandlerStop
    case_id = UUID(str(pending["caseId"]))
    try:
        result = await _core(context).review_reference_evaluation(
            case_id,
            actor_id,
            decision="CHANGES_REQUIRED",
            note=value,
            idempotency_key=UUID(str(pending.setdefault("idempotencyKey", uuid4()))),
        )
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        if isinstance(exc, LegalCoreApiError):
            await gateway_bot._reply(update, _friendly_error(exc))
        else:
            await gateway_bot._reply(
                update, "⚠️ Не удалось обработать замечание. Попробуйте ещё раз."
            )
        raise ApplicationHandlerStop from exc
    _clear_pending(context)
    status_value = (
        result.get("case", {}).get("status") if isinstance(result.get("case"), dict) else None
    )
    await gateway_bot._reply(
        update,
        f"✏️ Доработка запрошена. Новый статус: {_STATUS.get(str(status_value), 'обновлён')}.\n"
        "Автор увидит кнопку для подготовки новой версии.",
    )
    raise ApplicationHandlerStop


async def _receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = _pending(context)
    message = update.effective_message
    if pending is None or pending.get("stage") != "attachment" or message is None:
        return
    document = message.document
    actor_id = _actor_id(update)
    if document is None or actor_id is None or not document.file_name:
        await gateway_bot._reply(
            update, "Не удалось определить файл. Поддерживаются TXT, PDF и DOCX."
        )
        raise ApplicationHandlerStop
    if isinstance(document.file_size, int) and document.file_size > _MAX_UPLOAD_BYTES:
        await gateway_bot._reply(update, "Файл больше допустимого лимита 15 МБ.")
        raise ApplicationHandlerStop
    filename = document.file_name.strip()
    content_type = _EXTENSION_MIME.get(PurePosixPath(filename).suffix.casefold())
    declared_type = (document.mime_type or "application/octet-stream").split(";", 1)[0]
    if content_type is None or declared_type not in {content_type, "application/octet-stream"}:
        await gateway_bot._reply(update, "Поддерживаются только обезличенные TXT, PDF и DOCX.")
        raise ApplicationHandlerStop
    await gateway_bot._reply(update, "Проверяю обезличивание и безопасно сохраняю файл…")
    try:
        telegram_file = await context.bot.get_file(document.file_id)
        content = bytes(await telegram_file.download_as_bytearray())
        if not content or len(content) > _MAX_UPLOAD_BYTES:
            raise ValueError("invalid download size")
        await _core(context).upload_reference_evaluation_material(
            UUID(str(pending["caseId"])),
            actor_id,
            content=content,
            source_filename=filename,
            content_type=content_type,
            idempotency_key=UUID(str(pending.setdefault("idempotencyKey", uuid4()))),
        )
    except TelegramError as exc:
        await gateway_bot._reply(
            update, "⚠️ Не удалось получить файл из Telegram. Попробуйте ещё раз."
        )
        raise ApplicationHandlerStop from exc
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        if isinstance(exc, LegalCoreApiError):
            logger.warning("reference evaluation attachment rejected: %s", exc.code)
            await gateway_bot._reply(
                update, _friendly_error(exc), reply_markup=_attachment_keyboard()
            )
        else:
            await gateway_bot._reply(
                update, "⚠️ Не удалось безопасно сохранить файл. Попробуйте ещё раз."
            )
        raise ApplicationHandlerStop from exc
    pending["hasMaterial"] = True
    pending.pop("idempotencyKey", None)
    await gateway_bot._reply(
        update,
        "✅ Обезличенный материал добавлен к этой карточке. Он не является нормативным "
        "источником и не передаётся моделям.",
        reply_markup=_material_attached_keyboard(),
    )
    raise ApplicationHandlerStop


async def _save_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = _pending(context)
    actor_id = _actor_id(update)
    if pending is None or actor_id is None:
        return
    try:
        client = _core(context)
        operation_key = UUID(str(pending.setdefault("idempotencyKey", uuid4())))
        if pending.get("mode") == "revision":
            result = await client.revise_reference_evaluation(
                UUID(str(pending["caseId"])),
                actor_id,
                as_of_date=str(pending["asOfDate"]),
                expected_route=str(pending["expectedRoute"]),
                scenario_text=str(pending["scenarioText"]),
                idempotency_key=operation_key,
            )
        else:
            result = await client.create_reference_evaluation(
                actor_id,
                group_key=str(pending["groupKey"]),
                as_of_date=str(pending["asOfDate"]),
                expected_route=str(pending["expectedRoute"]),
                scenario_text=str(pending["scenarioText"]),
                idempotency_key=operation_key,
            )
        case = result.get("case")
        case_id = UUID(str(case["id"])) if isinstance(case, dict) else None
        if case_id is None:
            raise ValueError("invalid reference evaluation create response")
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        if isinstance(exc, LegalCoreApiError):
            await gateway_bot._reply(update, _friendly_error(exc), reply_markup=_summary_keyboard())
        else:
            await gateway_bot._reply(update, "⚠️ Не удалось сохранить кейс. Попробуйте ещё раз.")
        return
    pending.clear()
    pending.update({"mode": "attachment", "stage": "attachment", "caseId": str(case_id)})
    await gateway_bot._reply(
        update,
        "✅ Карточка сохранена. При желании отправьте один обезличенный TXT, PDF или DOCX "
        "до 15 МБ, либо направьте карточку юристу без вложения.",
        reply_markup=_attachment_keyboard(),
    )


async def _submit_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = _pending(context)
    actor_id = _actor_id(update)
    if pending is None or pending.get("stage") != "attachment" or actor_id is None:
        return
    try:
        case_id = UUID(str(pending["caseId"]))
        result = await _core(context).submit_reference_evaluation(
            case_id,
            actor_id,
            idempotency_key=UUID(str(pending.setdefault("submitIdempotencyKey", uuid4()))),
        )
    except (KeyError, ValueError, LegalCoreApiError) as exc:
        if isinstance(exc, LegalCoreApiError):
            await gateway_bot._reply(
                update, _friendly_error(exc), reply_markup=_attachment_keyboard()
            )
        else:
            await gateway_bot._reply(update, "⚠️ Не удалось направить кейс на проверку.")
        return
    _clear_pending(context)
    await gateway_bot._reply(
        update,
        "✅ Кейс направлен на проверку. Статус: "
        f"{_STATUS.get(str(result.get('status')), 'обновлён')}.\n\n"
        "До одобрения он не используется ни в анализах, ни в рекомендациях.",
        reply_markup=_keyboards(
            [[InlineKeyboardButton("🗂 К списку кейсов", callback_data="refeval:open")]]
        ),
    )


async def _review(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    case_id: UUID,
    decision: str,
) -> None:
    actor_id = _actor_id(update)
    if actor_id is None:
        return
    try:
        client = _core(context)
        detail = await client.get_reference_evaluation(case_id, actor_id)
        current_version = detail.get("currentVersion")
        if not isinstance(current_version, int) or current_version < 1:
            raise ValueError("reference evaluation version is invalid")
        result = await client.review_reference_evaluation(
            case_id,
            actor_id,
            decision=decision,
            note=None,
            idempotency_key=_stable_action_key(
                context,
                case_id=case_id,
                current_version=current_version,
                action=decision,
            ),
        )
    except (LegalCoreApiError, ValueError) as exc:
        if isinstance(exc, LegalCoreApiError):
            logger.warning("reference evaluation review failed: %s", exc.code)
            await gateway_bot._reply(update, _friendly_error(exc))
        else:
            await gateway_bot._reply(update, "⚠️ Не удалось проверить актуальную версию кейса.")
        return
    case = result.get("case")
    state = case.get("status") if isinstance(case, dict) else None
    action = "одобрен для будущей оценки" if decision == "APPROVE_FOR_EVALUATION" else "не принят"
    await gateway_bot._reply(
        update,
        f"✅ Кейс {action}. Статус: {_STATUS.get(str(state), 'обновлён')}.\n\n"
        "Это не меняет юридическую базу, политику рисков или текущие ответы бота.",
    )


async def _download_material(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    case_id: UUID,
) -> None:
    actor_id = _actor_id(update)
    message = update.effective_message
    if actor_id is None or message is None:
        return
    try:
        content, content_type = await _core(context).download_reference_evaluation_material(
            case_id, actor_id
        )
    except LegalCoreApiError as exc:
        await gateway_bot._reply(update, _friendly_error(exc))
        return
    extension = {
        "text/plain": "txt",
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    }[content_type]
    try:
        await message.reply_document(
            document=InputFile(
                BytesIO(content), filename=f"reference-evaluation-{case_id}.{extension}"
            ),
            caption=(
                "Обезличенный материал эталонного кейса. Не пересылайте его вне "
                "защищённого контура."
            ),
        )
    except TelegramError:
        await gateway_bot._reply(
            update, "⚠️ Не удалось передать файл через Telegram. Попробуйте позже."
        )


async def reference_evaluation_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if (
        query is None
        or not isinstance(query.data, str)
        or _CALLBACK_RE.fullmatch(query.data) is None
    ):
        return
    try:
        await query.answer()
    except BadRequest:
        return
    callback = query.data
    if callback == "refeval:open":
        _clear_pending(context)
        await _show_list(update, context)
    elif callback == "refeval:new":
        await _begin_new(update, context)
    elif callback.startswith("refeval:list:"):
        await _show_list(update, context, before=UUID(callback.rsplit(":", 1)[1]))
    elif callback.startswith("refeval:view:"):
        await _show_detail(update, context, UUID(callback.rsplit(":", 1)[1]))
    elif callback.startswith("refeval:download:"):
        await _download_material(update, context, UUID(callback.rsplit(":", 1)[1]))
    elif callback.startswith("refeval:revise:"):
        await _begin_revision(update, context, UUID(callback.rsplit(":", 1)[1]))
    elif callback.startswith("refeval:approve:"):
        await _review(update, context, UUID(callback.rsplit(":", 1)[1]), "APPROVE_FOR_EVALUATION")
    elif callback.startswith("refeval:reject:"):
        await _review(update, context, UUID(callback.rsplit(":", 1)[1]), "REJECT")
    elif callback.startswith("refeval:changes:"):
        _set_pending(
            context,
            {"mode": "review", "stage": "review_note", "caseId": callback.rsplit(":", 1)[1]},
        )
        await gateway_bot._reply(
            update,
            "Опишите, что необходимо уточнить или исправить "
            "(2–1 000 символов, без персональных данных).",
        )
    else:
        pending = _pending(context)
        if (
            callback.startswith("refeval:group:")
            and pending is not None
            and pending.get("stage") == "group"
        ):
            pending["groupKey"] = callback.rsplit(":", 1)[1]
            pending["stage"] = "route"
            await gateway_bot._reply(
                update,
                "Выберите ожидаемую безопасную обработку. Это проверочная метка, "
                "не решение по пациенту.",
                reply_markup=_routes_keyboard(),
            )
        elif (
            callback.startswith("refeval:route:")
            and pending is not None
            and pending.get("stage") == "route"
        ):
            pending["expectedRoute"] = callback.rsplit(":", 1)[1]
            pending["stage"] = "date"
            await gateway_bot._reply(
                update,
                "Укажите дату, на которую проверяется пример, в формате ГГГГ-ММ-ДД.",
                reply_markup=_date_keyboard(),
            )
        elif (
            callback == "refeval:confirm"
            and pending is not None
            and pending.get("stage") == "confirm"
        ):
            await _save_case(update, context)
        elif (
            callback == "refeval:attachment:skip"
            and pending is not None
            and pending.get("stage") == "attachment"
        ):
            await gateway_bot._reply(
                update,
                "Отправьте один обезличенный TXT, PDF или DOCX до 15 МБ. Если вложение не нужно, "
                "нажмите «Отправить юристу без файла».",
                reply_markup=_attachment_keyboard(),
            )
        elif callback == "refeval:attachment:submit":
            await _submit_case(update, context)
    raise ApplicationHandlerStop


def install_reference_evaluations(application: gateway_bot.TelegramApplication) -> None:
    """Install handlers ahead of generic text input but after privacy/navigation guards."""

    application.add_handler(CommandHandler("reference_cases", show_reference_evaluations), group=-5)
    application.add_handler(
        CallbackQueryHandler(reference_evaluation_callback, pattern=_CALLBACK_RE), group=-5
    )
    application.add_handler(MessageHandler(filters.Document.ALL, _receive_document), group=-5)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_text), group=-5
    )
