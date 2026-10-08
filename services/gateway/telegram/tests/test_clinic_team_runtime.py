# ruff: noqa: RUF001
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.clinic_team_ui import clinic_team_text


def workspace(
    role: str = "CLINIC_OWNER",
) -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    client = SimpleNamespace(
        get_actor=AsyncMock(return_value={"role": role}),
        list_clinic_members=AsyncMock(
            return_value={
                "items": [
                    {"telegramUserId": 111, "role": "CLINIC_OWNER"},
                    {"telegramUserId": 222, "role": "CLINIC_LAWYER"},
                ]
            }
        ),
        add_clinic_member=AsyncMock(return_value={"telegramUserId": 333, "role": "CLINIC_LAWYER"}),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=111),
        effective_message=SimpleNamespace(text="333", reply_text=AsyncMock()),
        callback_query=None,
    )
    context = SimpleNamespace(user_data={}, bot_data={bot.LEGAL_CORE_CLIENT_KEY: client})
    return update, context, client


def test_team_shows_existing_lawyer_and_add_buttons() -> None:
    update, context, _ = workspace()
    asyncio.run(bot.clinic_team(update, context))
    call = update.effective_message.reply_text.call_args
    assert "222 — юрист" in call.args[0]
    assert "111 — владелец" in call.args[0]
    callbacks = {
        b.callback_data for row in call.kwargs["reply_markup"].inline_keyboard for b in row
    }
    assert {"team:add:lawyer", "team:add:admin", "menu"} <= callbacks
    assert "HIGH/CRITICAL" in call.args[0]
    assert "редактора" in call.args[0]


@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "CLINIC_LAWYER"])
def test_non_owner_cannot_see_team(role: str) -> None:
    update, context, client = workspace(role)
    asyncio.run(bot.clinic_team(update, context))
    assert "222" not in update.effective_message.reply_text.call_args.args[0]
    assert "владельцу" in update.effective_message.reply_text.call_args.args[0]
    client.list_clinic_members.assert_not_awaited()


def test_team_api_failure_does_not_render_partial_members() -> None:
    update, context, client = workspace()
    client.list_clinic_members.side_effect = LegalCoreApiError(
        403, "CLINIC_OWNER_REQUIRED", "Denied"
    )
    asyncio.run(bot.clinic_team(update, context))
    assert "Не удалось" in update.effective_message.reply_text.call_args.args[0]
    assert "222" not in update.effective_message.reply_text.call_args.args[0]


def test_add_lawyer_refreshes_team_from_core() -> None:
    update, context, client = workspace()
    context.user_data[bot.TEAM_MEMBER_ROLE_KEY] = "CLINIC_LAWYER"
    client.list_clinic_members.return_value["items"].append(
        {"telegramUserId": 333, "role": "CLINIC_LAWYER"}
    )
    asyncio.run(bot.record_team_member(update, context))
    assert "333 — юрист" in update.effective_message.reply_text.call_args.args[0]
    assert bot.TEAM_MEMBER_ROLE_KEY not in context.user_data
    client.add_clinic_member.assert_awaited_once_with(111, 333, role="CLINIC_LAWYER")


def test_denied_add_lawyer_never_renders_team() -> None:
    update, context, client = workspace()
    context.user_data[bot.TEAM_MEMBER_ROLE_KEY] = "CLINIC_LAWYER"
    client.add_clinic_member.side_effect = LegalCoreApiError(403, "CLINIC_OWNER_REQUIRED", "Denied")
    asyncio.run(bot.record_team_member(update, context))
    assert "владельцу" in update.effective_message.reply_text.call_args.args[0]
    client.list_clinic_members.assert_not_awaited()


@pytest.mark.parametrize(
    "items",
    [
        None,
        ["invalid"],
        [
            {"telegramUserId": True, "role": "CLINIC_LAWYER"},
        ],
        [{"telegramUserId": 222, "role": "LEGAL_EDITOR"}],
    ],
)
def test_invalid_roster_is_rejected(items: object) -> None:
    with pytest.raises(ValueError):
        clinic_team_text(items)


def test_roster_is_bounded_and_does_not_show_other_fields() -> None:
    members = [
        {"telegramUserId": n, "role": "CLINIC_LAWYER", "privateNote": "hidden"}
        for n in range(1, 102)
    ]
    text = clinic_team_text(members)
    assert len(text) < 3900
    assert "• 50 — юрист" in text and "• 51 — юрист" not in text
    assert "hidden" not in text
    assert "Показаны первые 50" in text


def test_empty_roster_keeps_add_guidance() -> None:
    assert "Сотрудников пока нет" in clinic_team_text([])


def test_add_lawyer_prompt_has_back_button() -> None:
    update, context, _ = workspace()
    update.callback_query = SimpleNamespace(data="team:add:lawyer", answer=AsyncMock())
    asyncio.run(bot.prompt_team_member(update, context))
    assert context.user_data[bot.TEAM_MEMBER_ROLE_KEY] == "CLINIC_LAWYER"
    keyboard = update.effective_message.reply_text.call_args.kwargs["reply_markup"]
    assert any(b.callback_data == "menu" for row in keyboard.inline_keyboard for b in row)


@pytest.mark.parametrize("value", ["not-id", "0", "-1", "9223372036854775808"])
def test_invalid_lawyer_id_does_not_write(value: str) -> None:
    update, context, client = workspace()
    context.user_data[bot.TEAM_MEMBER_ROLE_KEY] = "CLINIC_LAWYER"
    update.effective_message.text = value
    asyncio.run(bot.record_team_member(update, context))
    client.add_clinic_member.assert_not_awaited()
