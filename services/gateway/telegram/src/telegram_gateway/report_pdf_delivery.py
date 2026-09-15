# ruff: noqa: RUF001
"""Authorized report PDFs using the editor's existing bounded file-delivery queue."""

from collections.abc import Awaitable, Callable
from io import BytesIO
from uuid import UUID

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes

from telegram_gateway import bot as gateway_bot
from telegram_gateway.editor_delivery import EditorFileDeliveryQueue
from telegram_gateway.ui import back_keyboard


async def queue_report_pdf(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    actor_id: int,
    resolve_report: Callable[[], Awaitable[UUID]],
    return_callback: str,
) -> None:
    """Resolve and authorize inside the worker, not before waiting in the queue.

    Only immutable IDs, the application clients and destination are captured. File completion
    must not restore or change whatever conversation the user opens while waiting.
    """
    queue = context.application.bot_data.get("legal_editor_file_deliveries")
    if not isinstance(queue, EditorFileDeliveryQueue):
        queue = EditorFileDeliveryQueue(context.application)
        context.application.bot_data["legal_editor_file_deliveries"] = queue
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("← К результату / карточке", callback_data=return_callback)],
            *back_keyboard().inline_keyboard,
        ]
    )
    bot = context.bot
    client = gateway_bot._legal_core(context)

    async def deliver() -> None:
        report_id = await resolve_report()
        await bot.send_message(
            chat_id=actor_id,
            text="📥 Готовлю PDF. Остальные меню доступны во время загрузки.",
            reply_markup=keyboard,
        )
        # This endpoint reauthorizes the report against the current actor/tenant as well.
        pdf = await client.download_pdf(report_id, actor_id)
        await bot.send_document(
            chat_id=actor_id,
            document=InputFile(BytesIO(pdf), filename=f"report-{report_id}.pdf"),
            caption="Канонический PDF-отчёт Legal Core.",
            reply_markup=keyboard,
        )

    async def failed() -> None:
        await bot.send_message(
            chat_id=actor_id,
            text="⚠️ PDF недоступен для этого аккаунта или не удалось загрузить его вовремя. "
            "Вернитесь к карточке и попробуйте ещё раз.",
            reply_markup=keyboard,
        )

    admission = queue.submit(actor_id, deliver, failed)
    if admission != "STARTED":
        await bot.send_message(
            chat_id=actor_id,
            text=(
                "📥 Ваш файл уже загружается; меню доступны."
                if admission == "DUPLICATE"
                else "⚠️ Очередь файлов занята. Попробуйте позже; меню доступны."
            ),
            reply_markup=keyboard,
        )
