# ruff: noqa: RUF001
"""Owner-only diagnostics, composed even when model execution is disabled."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

import httpx2
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes

from telegram_gateway import bot as gateway_bot
from telegram_gateway.legal_library_runtime import build_application_with_legal_library
from telegram_gateway.ui import back_keyboard

logger = logging.getLogger(__name__)

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


async def show_analysis_diagnostics(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    actor = gateway_bot._actor_id(update)
    if update.callback_query is not None:
        await update.callback_query.answer()
    if actor is None:
        raise ApplicationHandlerStop
    try:
        async with asyncio.timeout(6), httpx2.AsyncClient(
            base_url=gateway_bot.load_legal_core_url(), timeout=5,
            trust_env=False, follow_redirects=False,
        ) as client:
            response = await client.get(
                "/v1/analysis-diagnostics", headers={"X-Telegram-User-Id": str(actor)},
            )
        if response.status_code == 403:
            text = "Диагностика доступна владельцу клиники с активным доступом."
        elif response.status_code == 404:
            text = "Диагностика ещё не установлена в Legal Core. Обновите сервисы одной версией."
        else:
            response.raise_for_status()
            if len(response.content) > 16_384:
                raise ValueError("oversized diagnostics response")
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid diagnostics envelope")
            text = render_analysis_diagnostics(payload)
    except (httpx2.HTTPError, TimeoutError, ValueError):
        # Logs and messages contain neither response bodies nor endpoint/credential values.
        logger.warning("analysis diagnostics unavailable")
        text = "Не удалось получить диагностику Legal Core. Это не результат юридического анализа."
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Обновить диагностику", callback_data="analysis:service-status")],
        *back_keyboard().inline_keyboard,
    ])
    await gateway_bot._reply(update, text, reply_markup=keyboard)
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
