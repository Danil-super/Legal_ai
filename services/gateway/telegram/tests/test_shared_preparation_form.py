"""Both V2 entry paths resume confirmed facts without reopening attempted answers."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from telegram_gateway import bot
from telegram_gateway.guided_case_intake_v2 import active_states
from test_case_experience import harness
from test_case_wizard import FakeQuery
from test_dialog_isolation import DRAFT
from test_factual_safety_gateway import completed_data, runtime_context


@pytest.mark.parametrize("precision", ["UNKNOWN", "APPROXIMATE", "EXACT"])
def test_preparation_summary_keeps_unknown_and_approximate_attempts(precision):
    data = completed_data(representativeContact="UNKNOWN")
    data["eventDate"] = {"precision": precision,
                         "date": None if precision == "UNKNOWN" else "2026-09-01"}
    update, context, client, message = runtime_context(data, "case:v2:summary:confirm")
    before = deepcopy(data)
    state = asyncio.run(bot.confirm_v2_summary(update, context, preparation=True))
    assert state == bot.WizardState.V2_CONFIRM
    assert data["safetyScreening"] == before["safetyScreening"]
    assert data["eventDate"] == before["eventDate"]
    callbacks = [button.callback_data for markup in message.text_reply_markups if markup
                 for row in markup.inline_keyboard for button in row]
    assert f"case:confirm2:{data['workflow_id']}" in callbacks
    assert f"case:confirm:{data['workflow_id']}" not in callbacks
    assert client.steps == []


@pytest.mark.parametrize("checkpoint", active_states(completed_data()))
def test_generic_resume_skips_completed_checkpoints_in_a_confirmed_v2_draft(checkpoint):
    data = completed_data(representativeContact="UNKNOWN")
    update, context, client, message = runtime_context(data, "synthetic")
    draft_id = UUID(data["workflow_id"])
    original = bot._draft_payload(data)
    client.get_intake_draft = AsyncMock(return_value={
        "id": str(draft_id), "revision": 4, "wizardState": checkpoint,
        "draftData": deepcopy(original),
    })
    context.user_data.clear()  # New process has no conversation or local preparation handles.
    update.callback_query = FakeQuery(f"case:draft:{draft_id}")
    result = asyncio.run(bot.resume_intake_draft(update, context))
    assert result == bot.WizardState.SUMMARY
    saved = context.user_data[bot.WIZARD_DATA_KEY]
    assert bot._draft_payload(saved) == original
    assert saved[bot.DRAFT_STATE_KEY] == "SUMMARY"
    assert any("фабула" in text.lower() or "понята ситуация" in text.lower()
               for text in message.text_replies)


def test_installed_shared_form_routes_actual_summary_to_preparation_not_legacy_pdf():
    from telegram_gateway.preparation_form_runtime import install_preparation_form

    async def scenario():
        async with harness() as (app, core, sent, dispatch):
            install_preparation_form(app)
            data = completed_data(representativeContact="UNKNOWN")
            data["eventDate"] = {"date": None, "precision": "UNKNOWN"}
            core.draft = {"id": str(DRAFT), "revision": 3,
                          "wizardState": "INCOMING", "draftData": data}
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("case:v2:summary:confirm")
            assert core.draft["wizardState"] == "V2_CONFIRM"
            assert core.draft["draftData"] == data
            callbacks = [button.callback_data for item in sent if item.get("reply_markup")
                         for row in item["reply_markup"].inline_keyboard for button in row]
            assert f"case:confirm2:{DRAFT}" in callbacks
            assert f"case:confirm:{DRAFT}" not in callbacks
            assert not any("document" in item for item in sent)

    asyncio.run(scenario())
