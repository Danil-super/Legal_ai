"""Telegram regression tests for the de-identified reference-case workspace."""

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import UUID

import httpx2
import pytest
from telegram.ext import ApplicationHandlerStop
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreClient
from telegram_gateway.reference_evaluation_runtime import (
    PENDING_KEY,
    _receive_text,
    install_reference_evaluations,
    reference_evaluation_callback,
)

ACTOR = 7_000_000_001
CASE_ID = UUID("4d8d752e-f32c-4dc5-8f74-40bff8e80d5b")
TOKEN = "123456:unit_test_token_value_1234567890"


class FakeCore:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.reviews: list[dict[str, object]] = []
        self.submissions: list[UUID] = []

    async def get_reference_evaluation_access(self, actor: int) -> dict[str, bool]:
        assert actor == ACTOR
        return {"canContribute": True, "canReview": True}

    async def create_reference_evaluation(self, actor: int, **kwargs: object) -> dict[str, object]:
        assert actor == ACTOR
        self.created.append(kwargs)
        return {"case": {"id": str(CASE_ID)}}

    async def submit_reference_evaluation(
        self,
        case_id: UUID,
        actor: int,
        **_: object,
    ) -> dict[str, str]:
        assert actor == ACTOR
        self.submissions.append(case_id)
        return {"status": "READY_FOR_REVIEW"}

    async def review_reference_evaluation(
        self,
        case_id: UUID,
        actor: int,
        **kwargs: object,
    ) -> dict[str, object]:
        assert actor == ACTOR
        self.reviews.append({"case_id": case_id, **kwargs})
        return {"case": {"status": "CHANGES_REQUIRED"}}

    async def list_reference_evaluations(self, actor: int, **_: object) -> dict[str, object]:
        assert actor == ACTOR
        return {"items": [], "nextBefore": None}

    async def get_reference_evaluation(self, case_id: UUID, actor: int) -> dict[str, object]:
        assert case_id == CASE_ID
        assert actor == ACTOR
        return {"status": "CHANGES_REQUIRED", "createdBySelf": True, "currentVersion": 1}


def _context(core: FakeCore | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        bot_data={bot.LEGAL_CORE_CLIENT_KEY: core or FakeCore()},
        user_data={},
        bot=SimpleNamespace(get_file=AsyncMock()),
    )


def _update(*, text: str = "", callback: str | None = None) -> SimpleNamespace:
    message = SimpleNamespace(
        text=text, document=None, reply_text=AsyncMock(), reply_document=AsyncMock()
    )
    query = None if callback is None else SimpleNamespace(data=callback, answer=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=ACTOR),
        effective_message=message,
        callback_query=query,
    )


def test_reference_directory_keeps_long_titles_compact_and_has_next_page():
    core = FakeCore()
    core.list_reference_evaluations = AsyncMock(return_value={
        "items": [{"id": str(CASE_ID), "displayName": "Synthetic title " * 8, "status": "DRAFT"}],
        "nextBefore": str(CASE_ID),
    })
    update = _update(callback="refeval:open")
    asyncio.run(_callback(update, _context(core)))
    body = update.effective_message.reply_text.await_args.args[0]
    keyboard = update.effective_message.reply_text.await_args.kwargs["reply_markup"]
    assert len(body) < 500
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert all(len(button.text) <= 64 for button in buttons)
    assert any(button.callback_data == f"refeval:list:{CASE_ID}" for button in buttons)


async def _callback(update: SimpleNamespace, context: SimpleNamespace) -> None:
    with pytest.raises(ApplicationHandlerStop):
        await reference_evaluation_callback(update, context)  # type: ignore[arg-type]


async def _text(update: SimpleNamespace, context: SimpleNamespace) -> None:
    with pytest.raises(ApplicationHandlerStop):
        await _receive_text(update, context)  # type: ignore[arg-type]


def test_contributor_can_create_and_submit_deidentified_reference_case() -> None:
    async def scenario() -> None:
        core = FakeCore()
        context = _context(core)
        await _callback(_update(callback="refeval:new"), context)
        await _callback(_update(callback="refeval:group:healthcare"), context)
        await _callback(_update(callback="refeval:route:HUMAN_ESCALATION"), context)
        await _text(_update(text="2026-06-01"), context)
        await _text(
            _update(
                text="После лечения сохранилась чувствительность, клиника пригласила на осмотр."
            ),
            context,
        )
        await _callback(_update(callback="refeval:confirm"), context)

        pending = context.user_data[PENDING_KEY]
        assert pending["stage"] == "attachment"
        assert pending["caseId"] == str(CASE_ID)
        assert core.created == [
            {
                "group_key": "healthcare",
                "as_of_date": "2026-06-01",
                "expected_route": "HUMAN_ESCALATION",
                "scenario_text": (
                    "После лечения сохранилась чувствительность, клиника пригласила на осмотр."
                ),
                "idempotency_key": ANY,
            }
        ]

        await _callback(_update(callback="refeval:attachment:submit"), context)
        assert core.submissions == [CASE_ID]
        assert PENDING_KEY not in context.user_data

    asyncio.run(scenario())


def test_reviewer_note_is_deidentified_and_sets_changes_requested() -> None:
    async def scenario() -> None:
        core = FakeCore()
        context = _context(core)
        await _callback(_update(callback=f"refeval:changes:{CASE_ID}"), context)
        assert context.user_data[PENDING_KEY]["stage"] == "review_note"
        await _text(_update(text="Уточните последовательность действий клиники."), context)
        assert PENDING_KEY not in context.user_data
        assert core.reviews[0]["case_id"] == CASE_ID
        assert core.reviews[0]["decision"] == "CHANGES_REQUIRED"
        assert core.reviews[0]["note"] == "Уточните последовательность действий клиники."

    asyncio.run(scenario())


def test_repeated_reviewer_callback_reuses_the_same_idempotency_key() -> None:
    async def scenario() -> None:
        core = FakeCore()
        context = _context(core)

        await _callback(_update(callback=f"refeval:approve:{CASE_ID}"), context)
        await _callback(_update(callback=f"refeval:approve:{CASE_ID}"), context)

        assert [review["decision"] for review in core.reviews] == [
            "APPROVE_FOR_EVALUATION",
            "APPROVE_FOR_EVALUATION",
        ]
        assert core.reviews[0]["idempotency_key"] == core.reviews[1]["idempotency_key"]

    asyncio.run(scenario())


def test_reference_case_text_with_direct_identifier_is_never_sent_to_core() -> None:
    async def scenario() -> None:
        core = FakeCore()
        context = _context(core)
        context.user_data[PENDING_KEY] = {
            "mode": "create",
            "stage": "scenario",
            "groupKey": "healthcare",
            "asOfDate": "2026-06-01",
            "expectedRoute": "HUMAN_ESCALATION",
        }
        await _text(_update(text="Позвоните +7 999 123-45-67 и уточните ситуацию."), context)
        assert core.created == []
        assert context.user_data[PENDING_KEY]["stage"] == "scenario"

    asyncio.run(scenario())


def test_reference_handlers_are_installed_before_generic_text_input() -> None:
    application = bot.build_application(TOKEN)
    install_reference_evaluations(application)

    assert -5 in application.handlers
    names = {type(handler).__name__ for handler in application.handlers[-5]}
    assert {"CommandHandler", "CallbackQueryHandler", "MessageHandler"} <= names
    assert 0 in application.handlers


def test_reference_evaluation_gateway_client_uses_only_telegram_identity() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(201, json={"case": {"id": str(CASE_ID)}})

    async def scenario() -> None:
        async with httpx2.AsyncClient(
            base_url="http://legal-core:8000", transport=httpx2.MockTransport(handler)
        ) as http:
            await LegalCoreClient(http).create_reference_evaluation(
                ACTOR,
                group_key="healthcare",
                as_of_date="2026-06-01",
                expected_route="HUMAN_ESCALATION",
                scenario_text="Обезличенная фактическая ситуация для проверки.",
                idempotency_key=CASE_ID,
            )

    asyncio.run(scenario())

    assert requests[0].method == "POST"
    assert requests[0].url.path == "/v1/reference-evaluations"
    assert requests[0].headers["x-telegram-user-id"] == str(ACTOR)
    assert requests[0].headers["idempotency-key"] == str(CASE_ID)
    assert "x-clinic-id" not in requests[0].headers
    assert "clinicId" not in requests[0].content.decode()
