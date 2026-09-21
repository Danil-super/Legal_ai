# ruff: noqa: RUF001
"""Owner-only diagnostics, composed even when model execution is disabled."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, cast

import httpx2
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes

from telegram_gateway import bot as gateway_bot
from telegram_gateway.legal_library_runtime import build_application_with_legal_library
from telegram_gateway.ui import back_keyboard

logger = logging.getLogger(__name__)

DIAGNOSTICS_TASKS_KEY = "analysis_diagnostics_tasks"
DIAGNOSTICS_TIMEOUT_SECONDS = 6.0
TELEGRAM_DELIVERY_TIMEOUT_SECONDS = 3.0
CALLBACK_TIMEOUT_SECONDS = 1.0
MAX_PENDING_DIAGNOSTICS = 8
MAX_DIAGNOSTICS_BYTES = 16_384
_PROGRESS_TEXT = (
    "⏳ Проверяю состояние юридического анализа…\n"
    "Проверка Legal Core занимает не более 6 секунд. "
    "Модели не вызываются; другими командами можно пользоваться."
)
_UNAVAILABLE_TEXT = (
    "Не удалось получить диагностику Legal Core. Это не результат юридического анализа."
)
_TIMEOUT_TEXT = (
    "⚠️ Legal Core не ответил на диагностику за отведённое время (до 6 секунд).\n"
    "Это не означает, что анализ отключён. Остальные команды доступны; "
    "повторите проверку немного позже."
)

_RUNTIME_LABELS = {
    "DISABLED": "Отключён: сервер работает в режиме сбора обращений без вызова моделей.",
    "CONFIG_INVALID": "Не настроен: параметры сервиса анализа некорректны.",
    "UNREACHABLE": "Настроен, но оркестратор не отвечает на проверку доступности.",
    "REACHABLE": "Оркестратор доступен. Выполнение запроса к модели ещё не проверено.",
}
_POLICY_LABELS = {
    "APPROVED": "Есть одобренная политика риска.",
    "NOT_READY": "Одобренная политика риска не готова. Анализ будет заблокирован.",
}
_CORPUS_LABELS = {
    "PRESENT": "Есть одобренные источники, действующие сегодня.",
    "EMPTY": "Нет одобренных источников, действующих сегодня.",
}


def render_analysis_diagnostics(payload: dict[str, Any]) -> str:
    # Print only validated metadata and fixed labels. Never echo arbitrary error/config text.
    try:
        runtime = _RUNTIME_LABELS[payload["runtime"]]
        policy = _POLICY_LABELS[payload["riskPolicy"]]
        corpus = _CORPUS_LABELS[payload["legalCorpusToday"]]
        checked_at = datetime.fromisoformat(payload["checkedAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid diagnostics metadata") from exc
    revision = payload.get("sourceRevision")
    revision_label = (
        revision[:12]
        if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision)
        else "не передана сборкой"
    )
    last_report = payload.get("lastSuccessfulReportAt")
    if last_report is None:
        last_label = "не найден в сохранённых отчётах вашей клиники"
    else:
        try:
            last_label = datetime.fromisoformat(last_report).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid diagnostics timestamp") from exc
    return "\n".join([
        "ДИАГНОСТИКА ЮРИДИЧЕСКОГО АНАЛИЗА", "",
        f"Режим анализа: {runtime}", "",
        f"Политика риска: {policy}",
        f"Правовая база: {corpus}", "",
        f"Последний готовый анализ: {last_label}.",
        f"Версия кода (SHA): {revision_label}.",
        f"Проверено: {checked_at.isoformat()}.", "",
        "Наличие источников сегодня не подтверждает их применимость к дате вашего кейса.",
        "Проверка не вызывает модели и не проверяет их ключи. Для проверки всей цепочки "
        "нужен новый синтетический кейс: анкета → анализ → результат.",
        "Первый отчёт после анкеты — карточка обращения, а не юридическое заключение.",
    ])


def _diagnostics_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Обновить диагностику", callback_data="analysis:service-status")],
        *back_keyboard().inline_keyboard,
    ])


async def _fetch_diagnostics_text(actor: int) -> str:
    try:
        async with (
            asyncio.timeout(DIAGNOSTICS_TIMEOUT_SECONDS),
            httpx2.AsyncClient(
                base_url=gateway_bot.load_legal_core_url(), timeout=5,
                trust_env=False, follow_redirects=False,
            ) as client,
            client.stream(
                "GET", "/v1/analysis-diagnostics",
                headers={"X-Telegram-User-Id": str(actor), "Accept-Encoding": "identity"},
            ) as response,
        ):
            if response.status_code == 403:
                return "Диагностика доступна владельцу клиники с активным доступом."
            if response.status_code == 404:
                return (
                    "Диагностика ещё не установлена в Legal Core. "
                    "Обновите сервисы одной версией."
                )
            response.raise_for_status()
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ValueError("compressed diagnostics response")
            # Bound actual bytes before JSON decoding, not after client.get buffers them.
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > MAX_DIAGNOSTICS_BYTES:
                    raise ValueError("oversized diagnostics response")
                raw.extend(chunk)
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("invalid diagnostics envelope")
            return render_analysis_diagnostics(payload)
    except (TimeoutError, httpx2.TimeoutException):
        logger.warning("analysis diagnostics timed out")
        return _TIMEOUT_TEXT
    except (httpx2.HTTPError, ValueError, RecursionError):
        # Logs and messages contain neither response bodies nor endpoint/credential values.
        logger.warning("analysis diagnostics unavailable")
        return _UNAVAILABLE_TEXT


async def _answer_status_callback(update: Update, text: str | None = None) -> None:
    if update.callback_query is None:
        return
    try:
        async with asyncio.timeout(CALLBACK_TIMEOUT_SECONDS):
            await update.callback_query.answer(text=text)
    except (TelegramError, TimeoutError):
        # Expired callbacks must not suppress the actual diagnosis.
        logger.warning("analysis diagnostics callback acknowledgement unavailable")


async def _deliver_analysis_diagnostics(update: Update, actor: int) -> None:
    message = update.effective_message
    if message is None:
        return
    await _answer_status_callback(update)
    progress: Message | None = None
    try:
        async with asyncio.timeout(TELEGRAM_DELIVERY_TIMEOUT_SECONDS):
            progress = await message.reply_text(_PROGRESS_TEXT)
    except (TelegramError, TimeoutError):
        logger.warning("analysis diagnostics progress delivery unavailable")
    text = await _fetch_diagnostics_text(actor)
    if progress is not None:
        try:
            async with asyncio.timeout(TELEGRAM_DELIVERY_TIMEOUT_SECONDS):
                # Edit only our own progress message, never a menu the user has since opened.
                await progress.edit_text(text, reply_markup=_diagnostics_keyboard())
            return
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
        except (TelegramError, TimeoutError):
            logger.warning("analysis diagnostics result edit unavailable")
    try:
        async with asyncio.timeout(TELEGRAM_DELIVERY_TIMEOUT_SECONDS):
            await message.reply_text(text, reply_markup=_diagnostics_keyboard())
    except (TelegramError, TimeoutError):
        logger.warning("analysis diagnostics result delivery unavailable")


async def show_analysis_diagnostics(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    actor = gateway_bot._actor_id(update)
    chat = update.effective_chat
    if actor is None or chat is None or chat.type != "private":
        raise ApplicationHandlerStop
    application = context.application
    tasks = cast(
        dict[tuple[int, int], asyncio.Task[None]],
        application.bot_data.setdefault(DIAGNOSTICS_TASKS_KEY, {}),
    )
    # Coalesce refreshes only while running. Never cache another user's authorized result.
    for old_key, old_task in list(tasks.items()):
        if old_task.done():
            tasks.pop(old_key, None)
    key = (chat.id, actor)
    if key in tasks or len(tasks) >= MAX_PENDING_DIAGNOSTICS:
        notice = (
            "Проверка уже идёт. Результат появится в сообщении «Проверяю…»."
            if key in tasks else "Диагностика занята. Повторите проверку немного позже."
        )
        if update.callback_query is not None:
            await _answer_status_callback(update, notice)
        else:
            try:
                async with asyncio.timeout(CALLBACK_TIMEOUT_SECONDS):
                    await gateway_bot._reply(update, notice)
            except (TelegramError, TimeoutError):
                logger.warning("analysis diagnostics busy notice unavailable")
        raise ApplicationHandlerStop

    work = _deliver_analysis_diagnostics(update, actor)

    def forget(completed: asyncio.Task[None]) -> None:
        # PTB wraps the coroutine: pre-start cancellation otherwise leaves it unawaited.
        # The outer task is done, so closing either a finished or unstarted work is safe.
        work.close()
        if tasks.get(key) is completed:
            tasks.pop(key, None)

    task = application.create_task(
        work, update=update, name="analysis-status",
    )
    tasks[key] = task
    task.add_done_callback(forget)
    # Keep the dispatcher sequential for conversation state. Only this read-only I/O is
    # detached; block=False would make ApplicationHandlerStop ineffective in PTB.
    raise ApplicationHandlerStop


def build_application_with_diagnostics(token: str) -> gateway_bot.TelegramApplication:
    application = build_application_with_legal_library(token)
    application.add_handler(CommandHandler("analysis_status", show_analysis_diagnostics), group=-5)
    application.add_handler(CallbackQueryHandler(
        show_analysis_diagnostics, pattern=r"^analysis:service-status$",
    ), group=-5)
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    application = build_application_with_diagnostics(gateway_bot.load_token())
    application.run_polling(
        allowed_updates=gateway_bot.ALLOWED_UPDATES,
        bootstrap_retries=3, drop_pending_updates=False,
    )
