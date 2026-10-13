import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram_gateway import legal_library_runtime as runtime
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.ui import main_menu_keyboard


@pytest.mark.parametrize("role", ["CLINIC_OWNER", "CLINIC_LAWYER", "CLINIC_ADMIN", None])
def test_editor_has_one_legal_library_entry(role):
    buttons = [
        button
        for row in main_menu_keyboard(role, is_legal_editor=True).inline_keyboard
        for button in row
    ]
    assert sum(button.callback_data == "legalbase:open" for button in buttons) == 1
    assert not any(button.callback_data == "editor:open" for button in buttons)


@pytest.mark.parametrize("editor_allowed", [True, False])
def test_library_uses_editor_groups_only_when_core_authorizes_actor(monkeypatch, editor_allowed):
    groups = [{"key": "general", "title": "Synthetic group"}]
    payload = {
        "page": 1,
        "pageSize": 10,
        "totalItems": 0,
        "selectedGroup": None,
        "groups": groups,
        "items": [],
    }
    client = SimpleNamespace(
        _editor_gateway_key="synthetic-only-key",
        get_editor_groups=AsyncMock(
            return_value=payload,
            side_effect=None
            if editor_allowed
            else LegalCoreApiError(403, "LEGAL_EDITOR_NOT_ALLOWED", "Denied"),
        ),
        get_library_groups=AsyncMock(
            return_value={
                **payload,
                "asOfDate": "2026-10-12",
                "groups": {"general": "Synthetic group"},
                "hasMore": False,
            }
        ),
        get_library=AsyncMock(),
        aclose=AsyncMock(),
    )
    message = SimpleNamespace(reply_text=AsyncMock())
    monkeypatch.setattr(runtime, "LegalLibraryClient", lambda: client)
    monkeypatch.setattr(runtime.gateway_bot, "_actor_id", lambda _: 7000000001)
    asyncio.run(
        runtime._deliver_legal_library(
            SimpleNamespace(effective_message=message), SimpleNamespace()
        )
    )
    text = message.reply_text.await_args.args[0]
    callbacks = [
        button.callback_data
        for row in message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert "Synthetic group" not in text
    assert ("editor:group:general:1" in callbacks) is editor_allowed
    assert ("legalbase:group:general:1" in callbacks) is not editor_allowed
    client.get_library.assert_not_awaited()
    client.aclose.assert_awaited_once()


def test_grouped_read_only_library_has_no_approval_or_duplicate_titles():
    text, keyboard = runtime.render_grouped_legal_library(
        {
            "asOfDate": "2026-10-12",
            "page": 2,
            "selectedGroup": "general",
            "hasMore": True,
            "groups": {},
            "items": [
                {"documentTitle": "Synthetic approved law", "sourceUrl": "https://example.test/law"}
            ],
        }
    )
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert "Synthetic approved law" not in text
    assert buttons[0].text == "📄 Synthetic approved law"
    assert buttons[0].url == "https://example.test/law"
    assert "legalbase:group:general:1" in [button.callback_data for button in buttons]
    assert "legalbase:group:general:3" in [button.callback_data for button in buttons]
    assert not any((button.callback_data or "").startswith("editor:") for button in buttons)
    assert buttons[-1].callback_data == "menu"


def test_library_does_not_fallback_after_editor_server_error(monkeypatch):
    client = SimpleNamespace(
        _editor_gateway_key="synthetic-only-key",
        get_editor_groups=AsyncMock(side_effect=LegalCoreApiError(503, "UNAVAILABLE", "Failed")),
        get_library_groups=AsyncMock(),
        aclose=AsyncMock(),
    )
    reply = AsyncMock()
    monkeypatch.setattr(runtime, "LegalLibraryClient", lambda: client)
    monkeypatch.setattr(runtime.gateway_bot, "_actor_id", lambda _: 7000000001)
    monkeypatch.setattr(runtime.gateway_bot, "_reply", reply)
    asyncio.run(runtime._deliver_legal_library(SimpleNamespace(), SimpleNamespace()))
    client.get_library_groups.assert_not_awaited()
    assert reply.await_args.args[1].startswith("⚠️")
