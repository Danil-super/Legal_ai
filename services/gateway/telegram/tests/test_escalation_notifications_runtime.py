import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from telegram.error import Forbidden, RetryAfter, TimedOut
from telegram_gateway.escalation_notifications_runtime import (
    NOTIFICATIONS_CLIENT_KEY,
    notify_lawyers,
)


def test_notifications_are_installed_without_analysis_profile(monkeypatch):
    from telegram_gateway.analysis_runtime import build_application_with_analysis

    monkeypatch.delenv("AGENT_ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("AGENT_INTERNAL_KEY", raising=False)
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", "synthetic-gateway-key-" + "x" * 32)
    application = build_application_with_analysis("123456:" + "q" * 40)
    assert application.post_init.__module__.endswith("escalation_notifications_runtime")
    assert application.post_shutdown.__module__.endswith("escalation_notifications_runtime")


class Server:
    def __init__(self, recipients=(701, 702)):
        self.items = [
            {
                "notificationId": str(uuid4()),
                "leaseToken": str(uuid4()),
                "caseId": str(uuid4()),
                "escalationId": str(uuid4()),
                "riskLevel": "CRITICAL",
                "telegramUserId": recipient,
            }
            for recipient in recipients
        ]
        self.outcomes = []
        self.eligible = True

    async def claim(self):
        return {"items": self.items.copy()}

    async def check(self, item):
        return {"eligible": self.eligible}

    async def complete(self, item, outcome, retry_after=0):
        self.outcomes.append((item.notification_id, outcome, retry_after))
        if outcome in {"DELIVERED", "UNDELIVERABLE"}:
            self.items = [
                row for row in self.items if row["notificationId"] != str(item.notification_id)
            ]


class Bot:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    async def send_message(self, **kwargs):
        if self.error:
            raise self.error
        self.sent.append(kwargs)


def context(server, bot):
    return SimpleNamespace(bot_data={NOTIFICATIONS_CLIENT_KEY: server}, bot=bot)


def test_all_server_selected_lawyers_receive_only_opaque_case_and_risk():
    async def scenario():
        server, bot = Server(), Bot()
        await notify_lawyers(context(server, bot))
        assert {message["chat_id"] for message in bot.sent} == {701, 702}
        for message, item in zip(bot.sent, server.outcomes, strict=True):
            assert message["text"].startswith("⚖️ Новый кейс требует юриста")
            assert "CRITICAL" in message["text"]
            assert set(message) == {"chat_id", "text", "reply_markup"}
            assert item[1] == "DELIVERED"
        recovered = Bot()
        await notify_lawyers(context(server, recovered))
        assert recovered.sent == []

    asyncio.run(scenario())


def test_claimed_or_revoked_notification_is_rechecked_before_send():
    async def scenario():
        server, bot = Server(), Bot()
        server.eligible = False
        await notify_lawyers(context(server, bot))
        assert bot.sent == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error,outcome,minimum_delay",
    [
        (TimedOut(), "RETRY", 1),
        (RetryAfter(37), "RETRY", 37),
        (Forbidden("Bot was blocked by the user"), "UNDELIVERABLE", 0),
    ],
)
def test_telegram_failure_persists_retry_or_permanent_delivery_result(
    error, outcome, minimum_delay
):
    async def scenario():
        server, bot = Server((701,)), Bot(error)
        await notify_lawyers(context(server, bot))
        assert server.outcomes[0][1] == outcome
        assert server.outcomes[0][2] >= minimum_delay
        if outcome == "RETRY":
            recovered = Bot()
            await notify_lawyers(context(server, recovered))
            assert len(recovered.sent) == 1
            assert server.outcomes[-1][1] == "DELIVERED"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"riskLevel": "LOW"},
        {"riskLevel": []},
        {"telegramUserId": -123},
        {"telegramUserId": True},
        {"notificationId": "bad"},
    ],
)
def test_invalid_destination_or_risk_never_sends(change):
    async def scenario():
        server, bot = Server((701,)), Bot()
        server.items[0].update(change)
        await notify_lawyers(context(server, bot))
        assert bot.sent == []
        assert server.outcomes == []

    asyncio.run(scenario())
