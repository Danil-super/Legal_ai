import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from telegram.ext import ApplicationHandlerStop

from telegram_gateway.quick_intake import extract_quick_intake
from telegram_gateway.quick_intake_runtime import (
    _QUICK_CANDIDATE_KEY,
    _QUICK_PENDING_KEY,
    _continue_keyboard,
    _serialize_candidate,
    build_application_with_quick_intake,
    quick_candidate_callback,
    receive_quick_description,
    render_quick_candidate,
)

DRAFT_ID = UUID("00000000-0000-0000-0000-000000000123")
TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


class FakeMessage:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.replies: list[tuple[str, object | None]] = []

    async def reply_text(self, text: str, reply_markup: object | None = None) -> None:
        self.replies.append((text, reply_markup))


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answered = 0

    async def answer(self) -> None:
        self.answered += 1


class FakeLegalCoreClient:
    def __init__(self) -> None:
        self.created = 0
        self.saved: list[dict[str, object]] = []
        self.archived: list[tuple[UUID, int, int]] = []

    async def create_intake_draft(self, telegram_user_id: int) -> dict[str, object]:
        self.created += 1
        assert telegram_user_id == 777
        return {"id": str(DRAFT_ID), "revision": 1, "wizardState": "INCIDENT"}

    async def save_intake_draft(
        self,
        draft_id: UUID,
        telegram_user_id: int,
        *,
        expected_revision: int,
        wizard_state: str,
        draft_data: dict[str, object],
    ) -> dict[str, object]:
        assert draft_id == DRAFT_ID
        assert telegram_user_id == 777
        self.saved.append(
            {
                "expected_revision": expected_revision,
                "wizard_state": wizard_state,
                "draft_data": draft_data,
            }
        )
        return {
            "id": str(draft_id),
            "revision": expected_revision + 1,
            "wizardState": wizard_state,
            "draftData": draft_data,
        }

    async def archive_intake_draft(
        self,
        draft_id: UUID,
        telegram_user_id: int,
        *,
        expected_revision: int,
    ) -> dict[str, object]:
        self.archived.append((draft_id, telegram_user_id, expected_revision))
        return {"state": "ARCHIVED"}


def _context(client: FakeLegalCoreClient | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        user_data={},
        bot_data={"legal_core_client": client or FakeLegalCoreClient()},
    )


def _update(*, text: str | None = None, callback: str | None = None) -> SimpleNamespace:
    message = FakeMessage(text)
    query = None if callback is None else FakeQuery(callback)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=777),
        effective_message=message,
        callback_query=query,
    )


def test_render_quick_candidate_is_explicitly_non_legal_and_bounded() -> None:
    result = extract_quick_intake(
        "Скололся винир, пациент требует вернуть 70 тыс. руб. Письменной претензии нет."
    )

    rendered = render_quick_candidate(result)

    assert len(rendered) <= 3900
    assert "НЕ юридический анализ" in rendered
    assert "возврат денег" in rendered
    assert "70 000.00 ₽" in rendered
    assert "повторно подтверждены обычным wizard" in rendered


def test_continue_keyboard_targets_existing_durable_draft_entrypoint() -> None:
    keyboard = _continue_keyboard(DRAFT_ID)

    assert keyboard.inline_keyboard[0][0].callback_data == f"case:draft:{DRAFT_ID}"


def test_quick_description_does_not_intercept_text_when_mode_is_off() -> None:
    async def scenario() -> None:
        context = _context()
        update = _update(text="Обычный текст для существующего wizard.")

        result = await receive_quick_description(update, context)  # type: ignore[arg-type]

        assert result is None
        assert update.effective_message.replies == []
        assert _QUICK_CANDIDATE_KEY not in context.user_data

    asyncio.run(scenario())


def test_quick_description_stores_only_local_candidate_after_explicit_mode() -> None:
    async def scenario() -> None:
        context = _context()
        context.user_data[_QUICK_PENDING_KEY] = True
        update = _update(
            text="Скололся винир, пациент требует вернуть 70 тыс. руб. Телефон +7 999 123-45-67."
        )

        with pytest.raises(ApplicationHandlerStop):
            await receive_quick_description(update, context)  # type: ignore[arg-type]

        assert _QUICK_PENDING_KEY not in context.user_data
        stored = context.user_data[_QUICK_CANDIDATE_KEY]
        assert isinstance(stored, dict)
        assert "+7 999 123-45-67" not in str(stored)
        assert "[PHONE]" in str(stored)
        assert update.effective_message.replies
        assert "без LLM" in update.effective_message.replies[-1][0]

    asyncio.run(scenario())


def test_accept_creates_and_saves_real_durable_draft_prefix() -> None:
    async def scenario() -> None:
        client = FakeLegalCoreClient()
        context = _context(client)
        extracted = extract_quick_intake(
            "Скололся винир, пациент требует вернуть 70 тыс. руб. Письменной претензии нет."
        )
        context.user_data[_QUICK_CANDIDATE_KEY] = _serialize_candidate(extracted)
        update = _update(callback="quick:accept")

        with pytest.raises(ApplicationHandlerStop):
            await quick_candidate_callback(update, context)  # type: ignore[arg-type]

        assert client.created == 1
        assert len(client.saved) == 1
        saved = client.saved[0]
        assert saved["wizard_state"] == extracted.next_wizard_state
        assert saved["draft_data"] == extracted.draft_data
        assert client.archived == []
        assert _QUICK_CANDIDATE_KEY not in context.user_data
        assert update.callback_query.answered == 1
        assert update.effective_message.replies
        markup = update.effective_message.replies[-1][1]
        assert markup.inline_keyboard[0][0].callback_data == f"case:draft:{DRAFT_ID}"

    asyncio.run(scenario())


def test_application_registers_quick_handlers_before_existing_wizard() -> None:
    application = build_application_with_quick_intake(TOKEN)

    assert -3 in application.handlers
    handler_names = {type(handler).__name__ for handler in application.handlers[-3]}
    assert "CommandHandler" in handler_names
    assert "CallbackQueryHandler" in handler_names
    assert "MessageHandler" in handler_names
    assert 0 in application.handlers
