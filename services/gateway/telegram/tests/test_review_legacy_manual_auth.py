"""Independent adversarial checks for the historical manual entry point."""

import asyncio
from copy import deepcopy

import pytest
from telegram_gateway import bot
from telegram_gateway.understanding_intent import bound_callback
from test_confirmed_understanding_runtime import TEXT
from test_dialog_isolation import ACTOR, DRAFT
from test_intake_single_summary import simplified_harness


@pytest.mark.parametrize("ram_loss", [True, False])
def test_legacy_manual_with_reauthorization_denied_never_opens_or_mutates(ram_loss):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            if ram_loss:
                app.user_data[ACTOR].clear()
            previous = deepcopy(dict(app.user_data[ACTOR]))
            draft = deepcopy(core.draft)
            core.deny = True
            await dispatch(bound_callback("manual", operation, DRAFT))
            assert dict(app.user_data[ACTOR]) == previous
            assert core.draft == draft
            assert not core.cancels and not core.stages and not core.jobs and not core.accepts
            assert "Не удалось" in sent[-1]["text"]

    asyncio.run(scenario())


def test_legacy_manual_after_ram_loss_continues_without_final_consent_or_description_repeat():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            app.user_data[ACTOR].clear()
            await dispatch(bound_callback("manual", operation, DRAFT))
            assert core.cancels == [operation]
            assert core.draft["draftData"]["eventSummary"] == TEXT
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "INCOMING"
            # A historical or injected summary selector cannot create a case from
            # an incomplete draft merely because the manual entry was installed.
            await dispatch("case:v2:summary:confirm")
            assert not core.jobs and not core.accepts
            assert core.draft["draftData"]["eventSummary"] == TEXT
            assert not any("Кратко опишите, что произошло" in item.get("text", "")
                           for item in sent)

    asyncio.run(scenario())
