import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from telegram import InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError
from telegram.ext import CallbackQueryHandler, CommandHandler
from telegram_gateway.bot import (
    ADMIN_GRANT_ACCESS_KEY,
    ALLOWED_UPDATES,
    CALLBACK_ERROR_MESSAGE,
    POLLING_STALL_SECONDS,
    TELEGRAM_API_FAILURE_LIMIT,
    TELEGRAM_API_FAILURES_KEY,
    TELEGRAM_BOT_API_POOL_SIZE,
    TELEGRAM_POLLING_POOL_SIZE,
    _answer_callback,
    _keyboard,
    _polling_is_stalled,
    _polling_watchdog,
    _reply,
    admin_panel,
    build_application,
    delete_intake_draft_callback,
    help_command,
    load_telegram_proxy_url,
    load_token,
    menu_callback,
    on_error,
    on_startup,
    show_intake_drafts,
    start,
    text_input,
)
from telegram_gateway.ui import (
    AVATAR_IMAGE,
    BOT_DESCRIPTION,
    BOT_NAME,
    BOT_SHORT_DESCRIPTION,
    HELP_MESSAGE,
    MAIN_MENU_CALLBACKS,
    SCREENS,
    START_MESSAGE,
    TEXT_INPUT_DISABLED_MESSAGE,
    WELCOME_IMAGE,
    admin_panel_keyboard,
    main_menu_keyboard,
)
from telegram_gateway.update_processor import ActorSerialUpdateProcessor


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.text_replies: list[tuple[str, InlineKeyboardMarkup | None]] = []
        self.photo_replies: list[tuple[Path, str, InlineKeyboardMarkup]] = []

    async def reply_text(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        self.text_replies.append((text, reply_markup))

    async def reply_photo(
        self,
        photo: Path,
        caption: str,
        reply_markup: InlineKeyboardMarkup,
    ) -> None:
        self.photo_replies.append((photo, caption, reply_markup))


class FakeCallbackQuery:
    def __init__(
        self,
        data: object,
        *,
        edit_error: Exception | None = None,
        answer_error: Exception | None = None,
        message: object | None = None,
    ) -> None:
        self.data = data
        self.edit_error = edit_error
        self.answer_error = answer_error
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []
        self.edits: list[tuple[str, InlineKeyboardMarkup]] = []
        self.text_edits: list[tuple[str, InlineKeyboardMarkup]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        if self.answer_error is not None:
            raise self.answer_error
        self.answers.append((text, show_alert))

    async def edit_message_caption(
        self,
        caption: str,
        reply_markup: InlineKeyboardMarkup,
    ) -> None:
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append((caption, reply_markup))

    async def edit_message_text(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup,
    ) -> None:
        if self.edit_error is not None:
            raise self.edit_error
        self.text_edits.append((text, reply_markup))


class FakeUpdate:
    def __init__(
        self,
        message: FakeMessage | None = None,
        callback_query: FakeCallbackQuery | None = None,
        effective_user: object | None = None,
    ) -> None:
        self.effective_message = message
        self.callback_query = callback_query
        self.effective_user = effective_user


def test_load_token_returns_stripped_configured_value() -> None:
    token = load_token({"TELEGRAM_BOT_TOKEN": "  123456:unit_test_token_value_1234567890  "})

    assert token == "123456:unit_test_token_value_1234567890"


def test_reply_passes_an_optional_keyboard_to_telegram() -> None:
    message = FakeMessage()
    keyboard = admin_panel_keyboard()

    asyncio.run(_reply(FakeUpdate(message=message), "Выберите действие", reply_markup=keyboard))

    assert message.text_replies == [("Выберите действие", keyboard)]


@pytest.mark.parametrize(
    "environment",
    [{}, {"TELEGRAM_BOT_TOKEN": ""}, {"TELEGRAM_BOT_TOKEN": "not-a-token"}],
)
def test_load_token_rejects_missing_or_malformed_values(environment: dict[str, str]) -> None:
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        load_token(environment)


def test_load_telegram_proxy_url_accepts_an_internal_http_proxy() -> None:
    assert load_telegram_proxy_url({"TELEGRAM_PROXY_URL": " http://telegram-vpn-proxy:8080/ "}) == (
        "http://telegram-vpn-proxy:8080"
    )


@pytest.mark.parametrize(
    "value",
    [
        "socks5://telegram-vpn-proxy:8080",
        "http://user:password@telegram-vpn-proxy:8080",
        "http://telegram-vpn-proxy:8080/path",
        "http://telegram-vpn-proxy:8080?bypass=true",
        "http://telegram-vpn-proxy:8080#fragment",
    ],
)
def test_load_telegram_proxy_url_rejects_unsafe_or_unsupported_values(value: str) -> None:
    with pytest.raises(RuntimeError, match="TELEGRAM_PROXY_URL"):
        load_telegram_proxy_url({"TELEGRAM_PROXY_URL": value})


def test_build_application_routes_bot_api_and_polling_through_configured_proxy() -> None:
    proxy_url = "http://telegram-vpn-proxy:8080"

    application = build_application(
        "123456:unit_test_token_value_1234567890",
        proxy_url=proxy_url,
    )

    get_updates_request, bot_api_request = application.bot._request
    assert get_updates_request._client_kwargs["proxy"] == proxy_url
    assert bot_api_request._client_kwargs["proxy"] == proxy_url


def test_start_sends_branded_image_caption_and_main_menu() -> None:
    start_message = FakeMessage()

    asyncio.run(start(FakeUpdate(start_message), None))

    assert start_message.photo_replies == [
        (WELCOME_IMAGE, START_MESSAGE, main_menu_keyboard()),
    ]
    assert WELCOME_IMAGE.is_file()
    assert "персональ" in START_MESSAGE.lower()


def test_help_returns_concise_safety_scoped_instructions() -> None:
    help_message = FakeMessage()

    asyncio.run(help_command(FakeUpdate(help_message), None))

    assert help_message.text_replies == [(HELP_MESSAGE, None)]
    assert "чернов" in HELP_MESSAGE.lower()
    assert "/describe_case" in HELP_MESSAGE


def test_free_text_is_not_echoed_or_processed_before_case_core() -> None:
    sensitive_input = "Пациент Иван Иванов сообщил медицинские сведения"
    message = FakeMessage(sensitive_input)

    asyncio.run(text_input(FakeUpdate(message), None))

    assert message.text_replies == [(TEXT_INPUT_DISABLED_MESSAGE, None)]
    assert sensitive_input not in message.text_replies[0][0]


def test_main_menu_exposes_frequent_actions_as_clear_allowlisted_buttons() -> None:
    keyboard = main_menu_keyboard()
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert len(buttons) == 9
    assert {button.callback_data for button in buttons} <= MAIN_MENU_CALLBACKS
    assert {"case:start", "quick:start", "case:drafts", "account:id", "help"} <= {
        button.callback_data for button in buttons
    }
    assert all(button.text.strip() for button in buttons)
    assert all(
        isinstance(button.callback_data, str) and len(button.callback_data.encode()) <= 64
        for button in buttons
    )


def test_case_wizard_menus_always_offer_a_return_to_main_menu() -> None:
    keyboard = _keyboard([[("Выбрать вариант", "case:example")]])

    assert keyboard.inline_keyboard[-1][0].text == "← Главное меню"
    assert keyboard.inline_keyboard[-1][0].callback_data == "menu"


def test_draft_list_offers_confirmed_archive_for_the_callers_own_draft() -> None:
    draft_id = "00000000-0000-0000-0000-000000000123"

    class DraftClient:
        def __init__(self) -> None:
            self.archived: list[tuple[UUID, int, int]] = []

        async def list_intake_drafts(self, telegram_user_id: int) -> dict[str, object]:
            assert telegram_user_id == 7_000_000_001
            return {
                "items": [
                    {
                        "id": draft_id,
                        "wizardState": "INCIDENT",
                        "revision": 7,
                        "incidentType": "QUALITY_COMPLAINT",
                        "updatedAt": "2026-09-09T12:00:00Z",
                    }
                ]
            }

        async def get_intake_draft(
            self, received_draft_id: UUID, telegram_user_id: int
        ) -> dict[str, object]:
            assert received_draft_id == UUID(draft_id)
            assert telegram_user_id == 7_000_000_001
            return {"revision": 7}

        async def archive_intake_draft(
            self,
            received_draft_id: UUID,
            telegram_user_id: int,
            *,
            expected_revision: int,
        ) -> dict[str, object]:
            assert received_draft_id == UUID(draft_id)
            self.archived.append((received_draft_id, telegram_user_id, expected_revision))
            return {"revision": 8}

    client = DraftClient()
    context = SimpleNamespace(bot_data={"legal_core_client": client}, user_data={})
    list_message = FakeMessage()
    list_update = FakeUpdate(
        message=list_message,
        callback_query=FakeCallbackQuery("case:drafts"),
        effective_user=SimpleNamespace(id=7_000_000_001),
    )

    asyncio.run(show_intake_drafts(list_update, context))

    keyboard = list_message.text_replies[-1][1]
    assert keyboard is not None
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert f"case:draft:delete:{draft_id}" in callbacks

    confirm_query = FakeCallbackQuery(f"case:draft:delete:{draft_id}")
    confirm_update = FakeUpdate(
        callback_query=confirm_query,
        effective_user=SimpleNamespace(id=7_000_000_001),
    )
    asyncio.run(delete_intake_draft_callback(confirm_update, context))

    assert "УДАЛИТЬ ЧЕРНОВИК" in confirm_query.text_edits[-1][0]
    assert confirm_query.text_edits[-1][1].inline_keyboard[0][0].callback_data == (
        f"case:draft:delete:confirm:{draft_id}"
    )

    archive_query = FakeCallbackQuery(f"case:draft:delete:confirm:{draft_id}")
    archive_update = FakeUpdate(
        callback_query=archive_query,
        effective_user=SimpleNamespace(id=7_000_000_001),
    )
    asyncio.run(delete_intake_draft_callback(archive_update, context))

    assert client.archived == [(UUID(draft_id), 7_000_000_001, 7)]
    assert "удалён из активного списка" in archive_query.text_edits[-1][0]
    assert archive_query.text_edits[-1][1].inline_keyboard[-1][0].callback_data == "menu"


def test_lawyer_menu_exposes_only_review_workspace_actions() -> None:
    keyboard = main_menu_keyboard("CLINIC_LAWYER")
    callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row}

    assert "case:escalations" in callbacks
    assert "legalbase:open" in callbacks
    assert "case:start" not in callbacks
    assert "quick:start" not in callbacks
    assert "case:drafts" not in callbacks


def test_platform_editor_control_composes_with_the_clinic_lawyer_menu() -> None:
    keyboard = main_menu_keyboard("CLINIC_LAWYER", is_legal_editor=True)
    callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row}

    assert {"case:escalations", "legalbase:open", "editor:open"} <= callbacks


def test_platform_editor_control_is_not_present_without_editor_capability() -> None:
    keyboard = main_menu_keyboard("CLINIC_OWNER")
    callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row}

    assert "editor:open" not in callbacks


def test_administrator_menu_exposes_optional_clinic_document_library() -> None:
    keyboard = main_menu_keyboard("CLINIC_ADMIN")
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]

    assert "clinicdocs:open" in callbacks
    assert "legalbase:open" not in callbacks


def test_owner_menu_combines_administrator_and_lawyer_actions() -> None:
    keyboard = main_menu_keyboard("CLINIC_OWNER")
    callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row}

    assert {
        "case:start",
        "clinicdocs:open",
        "case:escalations",
        "legalbase:open",
        "team:open",
    } <= callbacks


def test_known_callback_answers_and_edits_the_welcome_caption() -> None:
    query = FakeCallbackQuery("privacy")

    asyncio.run(menu_callback(FakeUpdate(callback_query=query), None))

    assert query.answers == [(None, False)]
    assert query.edits[0][0] == SCREENS["privacy"]
    assert query.edits[0][1].inline_keyboard[0][0].callback_data == "menu"


def test_repeated_menu_callback_ignores_unchanged_caption_error() -> None:
    query = FakeCallbackQuery(
        "menu",
        edit_error=BadRequest("Message is not modified"),
    )

    asyncio.run(menu_callback(FakeUpdate(callback_query=query), None))

    assert query.answers == [(None, False)]


def test_expired_callback_is_ignored_without_failing_the_update() -> None:
    query = FakeCallbackQuery(
        "menu",
        answer_error=BadRequest("Query is too old and response timeout expired"),
    )

    result = asyncio.run(_answer_callback(FakeUpdate(callback_query=query)))

    assert result is None
    assert query.answers == []


def test_polling_stall_requires_a_backlog_and_an_expired_update_heartbeat() -> None:
    now = datetime(2026, 9, 9, 12, tzinfo=UTC)

    assert _polling_is_stalled(
        pending_update_count=1,
        heartbeat_at=now - timedelta(seconds=POLLING_STALL_SECONDS + 1),
        now=now,
    )
    assert not _polling_is_stalled(
        pending_update_count=0,
        heartbeat_at=now - timedelta(days=1),
        now=now,
    )
    assert not _polling_is_stalled(
        pending_update_count=1,
        heartbeat_at=now - timedelta(seconds=POLLING_STALL_SECONDS - 1),
        now=now,
    )


def test_menu_callback_edits_text_message_instead_of_a_missing_caption() -> None:
    query = FakeCallbackQuery("menu", message=SimpleNamespace(caption=None))

    asyncio.run(menu_callback(FakeUpdate(callback_query=query), None))

    assert query.text_edits[0][0] == START_MESSAGE


def test_menu_callback_does_not_hide_other_telegram_errors() -> None:
    query = FakeCallbackQuery(
        "menu",
        edit_error=BadRequest("Message to edit not found"),
    )

    with pytest.raises(BadRequest, match="not found"):
        asyncio.run(menu_callback(FakeUpdate(callback_query=query), None))


def test_callback_failure_is_reported_to_the_user() -> None:
    query = FakeCallbackQuery("menu")
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(error=RuntimeError("unexpected failure"))

    asyncio.run(on_error(update, context))

    assert query.answers == [
        (CALLBACK_ERROR_MESSAGE, True)
    ]


def test_repeated_telegram_network_errors_mark_gateway_unready_without_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ready_file = tmp_path / "ready"
    ready_file.touch()
    monkeypatch.setattr("telegram_gateway.bot.READY_FILE", ready_file)

    class RestartingApplication:
        def __init__(self) -> None:
            self.bot_data: dict[str, object] = {}
            self.stop_calls = 0

        def stop_running(self) -> None:
            self.stop_calls += 1

    application = RestartingApplication()
    context = SimpleNamespace(error=NetworkError("proxy unavailable"), application=application)

    for _ in range(TELEGRAM_API_FAILURE_LIMIT):
        asyncio.run(on_error(SimpleNamespace(callback_query=None), context))

    assert application.stop_calls == 0
    assert not ready_file.exists()
    assert application.bot_data[TELEGRAM_API_FAILURES_KEY] == TELEGRAM_API_FAILURE_LIMIT


def test_successful_watchdog_probe_resets_telegram_failure_counter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ready_file = tmp_path / "ready"
    heartbeat_file = tmp_path / "heartbeat"
    heartbeat_file.touch()
    monkeypatch.setattr("telegram_gateway.bot.READY_FILE", ready_file)
    monkeypatch.setattr("telegram_gateway.bot.POLLING_HEARTBEAT_FILE", heartbeat_file)

    class HealthyBot:
        async def get_webhook_info(self) -> object:
            return SimpleNamespace(pending_update_count=0)

    class HealthyApplication:
        bot = HealthyBot()

        def __init__(self) -> None:
            self.bot_data: dict[str, object] = {
                TELEGRAM_API_FAILURES_KEY: TELEGRAM_API_FAILURE_LIMIT - 1
            }
            self.stop_calls = 0

        def stop_running(self) -> None:
            self.stop_calls += 1

    application = HealthyApplication()
    asyncio.run(_polling_watchdog(SimpleNamespace(application=application)))

    assert application.stop_calls == 0
    assert ready_file.exists()
    assert TELEGRAM_API_FAILURES_KEY not in application.bot_data


def test_identity_button_displays_the_current_users_telegram_id() -> None:
    query = FakeCallbackQuery("account:id")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
    )

    asyncio.run(menu_callback(update, None))

    assert query.answers == [(None, False)]
    assert "7000000001" in query.edits[0][0]
    assert query.edits[0][1].inline_keyboard[0][0].callback_data == "menu"


def test_returning_to_menu_clears_pending_owner_access_input() -> None:
    query = FakeCallbackQuery("menu")
    context = SimpleNamespace(user_data={ADMIN_GRANT_ACCESS_KEY: True})

    asyncio.run(menu_callback(FakeUpdate(callback_query=query), context))

    assert context.user_data == {}


def test_admin_command_opens_a_separate_owner_workspace() -> None:
    message = FakeMessage()

    asyncio.run(admin_panel(FakeUpdate(message), None))

    assert "панель владельца" in message.text_replies[0][0].lower()
    assert message.text_replies[0][1] == admin_panel_keyboard()


@pytest.mark.parametrize("data", ["unknown", 42, None])
def test_untrusted_callback_data_is_rejected_without_editing(data: object) -> None:
    query = FakeCallbackQuery(data)

    asyncio.run(menu_callback(FakeUpdate(callback_query=query), None))

    assert query.answers == [("Меню обновилось. Откройте /menu.", True)]
    assert query.edits == []


def test_bot_profile_copy_and_captions_fit_telegram_limits() -> None:
    assert BOT_NAME == "Dental Legal AI"
    assert 1 <= len(BOT_SHORT_DESCRIPTION) <= 120
    assert 1 <= len(BOT_DESCRIPTION) <= 512
    assert all(len(caption) <= 1024 for caption in SCREENS.values())
    assert AVATAR_IMAGE.is_file()
    assert AVATAR_IMAGE.suffix == ".jpg"


def test_application_registers_callback_handler_and_required_update_types() -> None:
    application = build_application("123456:unit_test_token_value_1234567890")
    handlers: list[Any] = [handler for group in application.handlers.values() for handler in group]

    assert isinstance(application.update_processor, ActorSerialUpdateProcessor)
    assert any(isinstance(handler, CallbackQueryHandler) for handler in handlers)
    assert any(
        isinstance(handler, CallbackQueryHandler)
        and "case:draft:delete" in str(getattr(handler.pattern, "pattern", ""))
        for handler in handlers
    )
    assert any(
        isinstance(handler, CommandHandler) and "grant_access" in handler.commands
        for handler in handlers
    )
    assert any(
        isinstance(handler, CommandHandler) and "admin" in handler.commands
        for handler in handlers
    )
    assert ALLOWED_UPDATES == ["message", "callback_query"]


def test_application_reserves_connection_capacity_for_polling_and_bot_actions() -> None:
    application = build_application("123456:unit_test_token_value_1234567890")
    get_updates_request, bot_api_request = application.bot._request

    assert get_updates_request._client._transport._pool._max_connections == (  # type: ignore[attr-defined]
        TELEGRAM_POLLING_POOL_SIZE
    )
    assert bot_api_request._client._transport._pool._max_connections == (  # type: ignore[attr-defined]
        TELEGRAM_BOT_API_POOL_SIZE
    )


def test_updates_from_one_actor_remain_ordered_without_blocking_other_actors() -> None:
    async def scenario() -> list[str]:
        processor = ActorSerialUpdateProcessor(max_concurrent_updates=2)
        order: list[str] = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def first() -> None:
            order.append("first")
            first_started.set()
            await release_first.wait()

        async def second() -> None:
            order.append("second")

        async def other_actor() -> None:
            order.append("other")

        first_task = asyncio.create_task(
            processor.process_update(
                SimpleNamespace(effective_user=SimpleNamespace(id=101)),
                first(),
            )
        )
        await first_started.wait()
        second_task = asyncio.create_task(
            processor.process_update(
                SimpleNamespace(effective_user=SimpleNamespace(id=101)),
                second(),
            )
        )
        other_task = asyncio.create_task(
            processor.process_update(
                SimpleNamespace(effective_user=SimpleNamespace(id=202)), other_actor()
            )
        )
        await other_task
        assert order == ["first", "other"]

        release_first.set()
        await asyncio.gather(first_task, second_task)
        return order

    assert asyncio.run(scenario()) == ["first", "other", "second"]


def test_polling_startup_does_not_repeat_rate_limited_profile_mutations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ready_file = tmp_path / "ready"
    heartbeat_file = tmp_path / "heartbeat"
    monkeypatch.setattr("telegram_gateway.bot.READY_FILE", ready_file)
    monkeypatch.setattr("telegram_gateway.bot.POLLING_HEARTBEAT_FILE", heartbeat_file)

    class StartupJobQueue:
        task_name: str | None = None
        interval: int | None = None
        first: int | None = None

        def run_repeating(
            self,
            callback: object,
            *,
            interval: int,
            first: int,
            name: str,
        ) -> None:
            del callback
            self.task_name = name
            self.interval = interval
            self.first = first

    class StartupApplication:
        """No Bot API mutation methods are intentionally available here."""

        job_queue = StartupJobQueue()

    application = StartupApplication()
    asyncio.run(on_startup(application))  # type: ignore[arg-type]

    assert ready_file.is_file()
    assert heartbeat_file.is_file()
    assert application.job_queue.task_name == "telegram-polling-watchdog"
