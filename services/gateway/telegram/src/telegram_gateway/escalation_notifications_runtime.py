# ruff: noqa: RUF001
"""Restart-safe, privacy-minimal notifications to server-selected clinic lawyers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import httpx2
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError

logger = logging.getLogger(__name__)
NOTIFICATIONS_CLIENT_KEY = "escalation_notifications_client"
_PREFIX = "/v1/internal/escalation-notifications"


@dataclass(frozen=True)
class Notification:
    notification_id: UUID
    lease_token: UUID
    case_id: UUID
    escalation_id: UUID
    risk_level: str
    telegram_user_id: int

    @classmethod
    def parse(cls, payload: Any) -> Notification:
        if not isinstance(payload, dict):
            raise ValueError("invalid escalation notification")
        try:
            identifiers = [
                UUID(str(payload[key]))
                for key in ("notificationId", "leaseToken", "caseId", "escalationId")
            ]
            risk, recipient = payload["riskLevel"], payload["telegramUserId"]
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("invalid escalation notification") from exc
        if (
            not isinstance(risk, str)
            or risk not in {"HIGH", "CRITICAL"}
            or type(recipient) is not int
            or recipient <= 0
        ):
            raise ValueError("invalid escalation notification destination or risk")
        return cls(identifiers[0], identifiers[1], identifiers[2], identifiers[3], risk, recipient)


class EscalationNotificationsClient:
    def __init__(self, http: httpx2.AsyncClient, gateway_key: str):
        self.http, self.gateway_key = http, gateway_key

    async def request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with asyncio.timeout(6):
                response = await self.http.post(
                    path,
                    headers={"X-Legal-Editor-Gateway-Key": self.gateway_key},
                    json=payload,
                )
        except (httpx2.HTTPError, TimeoutError) as exc:
            raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "Core unavailable") from exc
        if response.status_code >= 400:
            raise LegalCoreApiError(
                response.status_code, "NOTIFICATION_UNAVAILABLE", "Notification rejected"
            )
        if response.status_code == 204:
            return {}
        try:
            body = response.json()
        except ValueError as exc:
            raise ValueError("invalid notification response") from exc
        if not isinstance(body, dict):
            raise ValueError("invalid notification response")
        return body

    async def claim(self) -> dict[str, Any]:
        return await self.request(f"{_PREFIX}/claims")

    async def check(self, item: Notification) -> dict[str, Any]:
        return await self.request(
            f"{_PREFIX}/{item.notification_id}/delivery-check",
            {"leaseToken": str(item.lease_token)},
        )

    async def complete(self, item: Notification, outcome: str, retry_after: int = 0) -> None:
        await self.request(
            f"{_PREFIX}/{item.notification_id}/delivery-results",
            {
                "leaseToken": str(item.lease_token),
                "outcome": outcome,
                "retryAfterSeconds": retry_after,
            },
        )


async def notify_lawyers(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Claim a bounded batch, recheck eligibility and persist each delivery outcome."""
    client = cast(EscalationNotificationsClient, context.bot_data[NOTIFICATIONS_CLIENT_KEY])
    try:
        payload = await client.claim()
        items = payload.get("items")
        if not isinstance(items, list) or len(items) > 20:
            raise ValueError("invalid notification batch")
    except (LegalCoreApiError, ValueError) as exc:
        logger.warning("escalation notification discovery deferred: %s", type(exc).__name__)
        return

    async def deliver(raw: Any) -> None:
        try:
            item = Notification.parse(raw)
            checked = await client.check(item)
            if checked.get("eligible") is not True:
                return
            outcome, delay = "DELIVERED", 0
            try:
                async with asyncio.timeout(8):
                    await context.bot.send_message(
                        chat_id=item.telegram_user_id,
                        text=(
                            "⚖️ Новый кейс требует юриста\n\n"
                            f"Кейс: {item.case_id}\nРиск: {item.risk_level}"
                        ),
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "⚖️ Открыть кейс",
                                        callback_data=f"case:escalation:{item.escalation_id}",
                                    )
                                ],
                            ]
                        ),
                    )
            except RetryAfter as exc:
                seconds = (
                    exc.retry_after.total_seconds()
                    if isinstance(exc.retry_after, timedelta)
                    else exc.retry_after
                )
                outcome, delay = "RETRY", min(86400, max(1, int(seconds) + 1))
            except Forbidden:
                outcome = "UNDELIVERABLE"
            except (TelegramError, TimeoutError):
                outcome, delay = "RETRY", 5
            await client.complete(item, outcome, delay)
        except (LegalCoreApiError, ValueError) as exc:
            # Lease expiry recovers an interrupted send or a lost ACK on the next process.
            # Logs contain neither Telegram identifiers nor case text nor response bodies.
            logger.warning("escalation notification deferred: %s", type(exc).__name__)

    try:
        async with asyncio.timeout(25):
            # Keep Telegram request concurrency bounded independently of case conversations.
            semaphore = asyncio.Semaphore(4)

            async def bounded(raw: Any) -> None:
                async with semaphore:
                    await deliver(raw)

            await asyncio.gather(*(bounded(raw) for raw in items))
    except TimeoutError:
        logger.warning("escalation notification batch reached time budget")


def install_escalation_notifications(
    application: gateway_bot.TelegramApplication, gateway_key: str
) -> None:
    previous_init, previous_shutdown = application.post_init, application.post_shutdown

    async def initialize(app: gateway_bot.TelegramApplication) -> None:
        if previous_init is not None:
            await previous_init(app)
        app.bot_data[NOTIFICATIONS_CLIENT_KEY] = EscalationNotificationsClient(
            httpx2.AsyncClient(
                base_url=gateway_bot.load_legal_core_url(),
                timeout=5,
                follow_redirects=False,
                trust_env=False,
                limits=httpx2.Limits(max_connections=8, max_keepalive_connections=4),
            ),
            gateway_key,
        )
        if app.job_queue is None:
            raise RuntimeError("escalation notifications require JobQueue")
        app.job_queue.run_repeating(
            notify_lawyers,
            interval=2,
            first=1,
            name="durable-escalation-notifications",
            job_kwargs={"max_instances": 1, "coalesce": True},
        )

    async def shutdown(app: gateway_bot.TelegramApplication) -> None:
        client = app.bot_data.pop(NOTIFICATIONS_CLIENT_KEY, None)
        if client is not None:
            await client.http.aclose()
        if previous_shutdown is not None:
            await previous_shutdown(app)

    application.post_init, application.post_shutdown = initialize, shutdown
