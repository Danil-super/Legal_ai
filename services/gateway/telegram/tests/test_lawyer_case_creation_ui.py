"""A clinic lawyer can enter the same narrative-first case flow as other creators."""

import asyncio
from unittest.mock import AsyncMock

from telegram_gateway.ui import main_menu_keyboard
from test_intake_single_summary import simplified_harness


def test_lawyer_menu_exposes_case_creation_without_clinic_management():
    markup = main_menu_keyboard("CLINIC_LAWYER", is_legal_editor=True)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert {"quick:start", "case:drafts", "case:escalations", "legalbase:open"} <= set(callbacks)
    assert "team:open" not in callbacks
    assert "clinicdocs:open" not in callbacks
    assert callbacks.count("quick:start") == 1


def test_active_lawyer_starts_a_v2_draft_and_sends_description_for_understanding():
    async def scenario():
        async with simplified_harness() as (_app, core, _sent, dispatch):
            core.get_actor = AsyncMock(return_value={"role": "CLINIC_LAWYER"})
            await dispatch("quick:start")
            await dispatch("text:Учебная ситуация: посетитель просит вернуть 5000 рублей.")
            assert core.draft["draftData"]["intakeVersion"] == 2
            assert "5000" in core.draft["draftData"]["eventSummary"]
            assert len(core.starts) == 1
            assert not core.accepts and not core.jobs
    asyncio.run(scenario())
