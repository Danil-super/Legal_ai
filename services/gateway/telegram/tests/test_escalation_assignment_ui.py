"""Transfer requires owner selection and confirmation, never a mere card open."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from telegram.ext import ApplicationHandlerStop
from telegram_gateway.escalation_workspace import (
    render_case_card,
    show_workspace,
    workspace_action,
    workspace_keyboard,
)

ESC = UUID("00000000-0000-0000-0000-000000000321")
OWNER = UUID("00000000-0000-0000-0000-000000000322")
LAWYER = UUID("00000000-0000-0000-0000-000000000323")


def setup(role="CLINIC_OWNER"):
    detail = {"publicNumber": "DL-TEST-26", "status": "IN_PROGRESS", "riskLevel": "HIGH",
              "assignedMembershipId": str(OWNER), "assignedToMe": role == "CLINIC_OWNER",
              "assignedRole": "CLINIC_OWNER", "facts": {}, "report": None}
    core = SimpleNamespace(
        get_actor=AsyncMock(return_value={"role": role}),
        get_escalation=AsyncMock(return_value=detail),
        get_escalation_discussion=AsyncMock(return_value={"items": []}),
        list_clinic_members=AsyncMock(return_value={"items": [
            {"membershipId": str(LAWYER), "telegramUserId": 222, "role": "CLINIC_LAWYER"},
        ]}), assign_escalation=AsyncMock(),
    )
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=111), effective_message=message,
                             callback_query=SimpleNamespace(data="", answer=AsyncMock()))
    context = SimpleNamespace(user_data={}, bot_data={"legal_core_client": core})
    return update, context, core


async def action(update, context, callback):
    update.callback_query.data = callback
    with pytest.raises(ApplicationHandlerStop):
        await workspace_action(update, context)


def callback(update, prefix):
    for call in reversed(update.effective_message.reply_text.call_args_list):
        markup = call.kwargs.get("reply_markup")
        if markup:
            for row in markup.inline_keyboard:
                for button in row:
                    if (isinstance(button.callback_data, str)
                            and button.callback_data.startswith(prefix)):
                        assert len(button.callback_data.encode()) <= 64
                        return button.callback_data
    raise AssertionError(f"missing {prefix} button")


def test_owner_can_choose_and_confirm_without_assigning_on_preview():
    async def scenario():
        update, context, core = setup()
        await action(update, context, f"esc:assign:{ESC}")
        core.assign_escalation.assert_not_awaited()
        await action(update, context, callback(update, "esc:pick:"))
        core.assign_escalation.assert_not_awaited()
        await action(update, context, callback(update, "esc:confirm-assign:"))
        core.assign_escalation.assert_awaited_once_with(
            ESC, 111, assigned_membership_id=LAWYER, expected_assigned_membership_id=OWNER,
        )
    asyncio.run(scenario())


def test_non_owner_cannot_open_assignment_even_with_forged_callback():
    async def scenario():
        update, context, core = setup("CLINIC_LAWYER")
        await action(update, context, f"esc:assign:{ESC}")
        core.list_clinic_members.assert_not_awaited()
        core.assign_escalation.assert_not_awaited()
    asyncio.run(scenario())


def test_old_choice_is_invalid_after_starting_another_assignment():
    async def scenario():
        update, context, core = setup()
        await action(update, context, f"esc:assign:{ESC}")
        old = callback(update, "esc:pick:")
        await action(update, context, f"esc:assign:{ESC}")
        await action(update, context, old)
        core.assign_escalation.assert_not_awaited()
        assert "недействительна" in update.effective_message.reply_text.call_args.args[0]
    asyncio.run(scenario())


def test_owner_has_transfer_action_but_lawyer_does_not():
    _, _, core = setup()
    detail = core.get_escalation.return_value
    buttons = workspace_keyboard(ESC, detail, can_manage=True, can_assign=True).inline_keyboard
    assert any(b.callback_data == f"esc:assign:{ESC}" for row in buttons for b in row)
    buttons = workspace_keyboard(ESC, detail, can_manage=True).inline_keyboard
    assert not any(str(b.callback_data).startswith("esc:assign:") for row in buttons for b in row)


def test_card_distinguishes_assigned_owner_from_another_lawyer():
    _, _, core = setup("CLINIC_LAWYER")
    assert "Ответственный: владелец клиники" in render_case_card(core.get_escalation.return_value)


def test_returning_to_card_cancels_pending_resolution():
    async def scenario():
        update, context, _ = setup()
        context.user_data["escalation_resolution_pending"] = str(ESC)
        await show_workspace(update, context, ESC)
        assert "escalation_resolution_pending" not in context.user_data
    asyncio.run(scenario())


def test_starting_assignment_cancels_an_unsubmitted_conclusion():
    async def scenario():
        update, context, core = setup()
        context.user_data["escalation_resolution_pending"] = str(ESC)
        context.user_data["escalation_discussion_id"] = str(ESC)
        await action(update, context, f"esc:assign:{ESC}")
        assert "escalation_resolution_pending" not in context.user_data
        assert "escalation_discussion_id" not in context.user_data
        core.assign_escalation.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["expired", "unselected", "forged", "cancelled"])
def test_invalid_or_cancelled_confirmation_never_assigns(invalid):
    async def scenario():
        update, context, core = setup()
        await action(update, context, f"esc:assign:{ESC}")
        pick = callback(update, "esc:pick:")
        if invalid != "unselected":
            await action(update, context, pick)
        confirm = pick.replace("esc:pick:", "esc:confirm-assign:")
        if invalid == "expired":
            context.user_data["escalation_assignment_pending"]["expires"] = 0
        elif invalid == "forged":
            confirm = "esc:confirm-assign:" + "0" * 32
        elif invalid == "cancelled":
            await show_workspace(update, context, ESC)
        await action(update, context, confirm)
        core.assign_escalation.assert_not_awaited()
    asyncio.run(scenario())
