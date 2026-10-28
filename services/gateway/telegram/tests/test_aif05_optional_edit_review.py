"""Independent real PTB regression: saved selection is not the DONE decision."""

import asyncio

import pytest
from telegram_gateway import bot
from test_aif05_material_question_reproductions import known_data
from test_dialog_isolation import ACTOR
from test_intake_single_summary import simplified_harness
from test_intake_transient_cleanup import delivery_finished


@pytest.mark.parametrize("prefilled", [False, True])
def test_optional_action_selection_resumes_until_done_then_returns_to_summary(prefilled):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:Учебная ситуация: пациент просит вернуть 5000 рублей.")
            proposed = known_data()
            for field in ("intakeVersion", "eventSummary", "incomingSourceStatus",
                          "caseMaterialsStatus", "conflictStage", "clinicActions"):
                proposed.pop(field)
            if prefilled:
                proposed["clinicActions"] = ["INVITED_FOR_EXAMINATION"]
            core.propose(**proposed)
            await delivery_finished(app)
            assert core.draft["wizardState"] == "SUMMARY"

            await dispatch("case:v2:context:actions")
            await dispatch("case:v2:multi:clinicActions:OFFERED_REFUND")
            assert core.draft["wizardState"] == "CLINIC_ACTIONS"
            chosen = list(core.draft["draftData"]["clinicActions"])
            assert "OFFERED_REFUND" in chosen
            if prefilled:
                assert "INVITED_FOR_EXAMINATION" in chosen

            await dispatch("/menu")
            await dispatch("case:draft:" + core.draft["id"])
            local = app.user_data[ACTOR][bot.WIZARD_DATA_KEY]
            assert local[bot.DRAFT_STATE_KEY] == "CLINIC_ACTIONS"
            assert core.draft["wizardState"] == "CLINIC_ACTIONS"
            assert local["clinicActions"] == chosen
            callbacks = {button.callback_data
                         for row in sent[-1]["reply_markup"].inline_keyboard for button in row}
            assert "case:v2:multi:clinicActions:DONE" in callbacks
            assert not core.accepts and not core.jobs

            await dispatch("case:v2:multi:clinicActions:DONE")
            assert core.draft["wizardState"] == "SUMMARY"
            await dispatch("/menu")
            await dispatch("case:draft:" + core.draft["id"])
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SUMMARY"
            assert core.draft["draftData"]["clinicActions"] == chosen
            assert not core.accepts and not core.jobs

    asyncio.run(scenario())
