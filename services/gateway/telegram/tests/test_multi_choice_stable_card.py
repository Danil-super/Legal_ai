"""One checklist card stays until Continue; its explicit add/remove clicks are idempotent."""

import asyncio
from copy import deepcopy
from unittest.mock import patch

import pytest
from telegram.error import NetworkError
from telegram.ext import ExtBot
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from test_answer_staged_recovery import amount_draft
from test_dialog_isolation import ACTOR
from test_intake_transient_cleanup import cleanup_harness, message_with
from test_scenario_gateway_audit_20261014 import dispatch_raw


def test_multi_choices_edit_one_card_and_continue_deletes_it_after_persisted_progress():
    async def run():
        async with cleanup_harness() as (app, core, sent, dispatch, visible, deleted,
                                         events, _failures):
            data = amount_draft()
            data.pop("clinicActions")
            data.pop("healthSignals")
            data.pop("safetyScreening")
            core.draft.update(wizardState="CLINIC_ACTIONS", draftData=data)
            await dispatch("case:draft:" + core.draft["id"])
            card = message_with(visible, "Что клиника уже сделала")
            before = len(sent)
            for choice in ("INVITED_FOR_EXAMINATION", "CONTINUING_TREATMENT"):
                await dispatch_raw(app, content="case:v2:multi:clinicActions:+" + choice,
                                   message_id=card)
            assert core.draft["draftData"]["clinicActions"] == [
                "INVITED_FOR_EXAMINATION", "CONTINUING_TREATMENT",
            ]
            assert card not in deleted and len(sent) == before
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY]["_v2_prompt_message_id"] == card
            markup = sent[-1]["reply_markup"]
            assert any(button.text == "Продолжить →" for row in markup.inline_keyboard
                       for button in row)
            snapshot = deepcopy(core.draft)
            await dispatch_raw(app, content="case:v2:multi:clinicActions:+INVITED_FOR_EXAMINATION",
                               message_id=card, update_id=1001)
            assert core.draft == snapshot, "replayed add must not undo a selected item"
            await dispatch_raw(app, content="case:v2:multi:clinicActions:DONE", message_id=card,
                               update_id=1002)
            assert card in deleted
            assert len(sent) > before and any(kind == "delete" for kind, *_rest in events)
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["save", "wrong-receipt", "edit"])
def test_checklist_failures_keep_existing_card_and_offer_safe_recovery(failure):
    async def run():
        async with cleanup_harness() as (app, core, sent, dispatch, visible, deleted,
                                         _events, _failures):
            data = amount_draft()
            data.pop("clinicActions")
            core.draft.update(wizardState="CLINIC_ACTIONS", draftData=data)
            await dispatch("case:draft:" + core.draft["id"])
            card = message_with(visible, "Что клиника уже сделала")
            before = deepcopy(core.draft)
            save = core.save_intake_draft
            transport = ExtBot._post
            edits = []

            async def broken_save(*args, **kwargs):
                if failure == "save":
                    raise LegalCoreApiError(503, "UNAVAILABLE", "Synthetic save failure")
                saved = await save(*args, **kwargs)
                if failure == "wrong-receipt":
                    saved["id"] = "00000000-0000-0000-0000-000000000987"
                return saved

            async def broken_edit(self, endpoint, data=None, **kwargs):
                if endpoint == "editMessageReplyMarkup":
                    edits.append(data)
                    if failure == "edit":
                        raise NetworkError("Synthetic edit failure")
                return await transport(self, endpoint, data=data, **kwargs)

            core.save_intake_draft = broken_save
            with patch.object(ExtBot, "_post", broken_edit):
                await dispatch_raw(app, content="case:v2:multi:clinicActions:+CONTINUING_TREATMENT",
                                   message_id=card)

            assert card not in deleted
            if failure == "save":
                assert core.draft == before and not edits
            elif failure == "wrong-receipt":
                assert not edits, "do not show a selection acknowledged for a different draft"
            else:
                assert core.draft["draftData"]["clinicActions"] == ["CONTINUING_TREATMENT"]
                assert any(button.callback_data == "case:draft:" + core.draft["id"]
                           for item in sent if item.get("reply_markup")
                           for row in item["reply_markup"].inline_keyboard for button in row)

    asyncio.run(run())


def test_remove_replay_and_cross_field_click_cannot_change_other_facts():
    async def run():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         _events, _failures):
            data = amount_draft()
            data.pop("clinicActions")
            data.pop("healthSignals")
            data.pop("safetyScreening")
            core.draft.update(wizardState="CLINIC_ACTIONS", draftData=data)
            await dispatch("case:draft:" + core.draft["id"])
            card = message_with(visible, "Что клиника уже сделала")
            await dispatch_raw(app, content="case:v2:multi:clinicActions:+INVITED_FOR_EXAMINATION",
                               message_id=card)
            await dispatch_raw(app, content="case:v2:multi:clinicActions:-INVITED_FOR_EXAMINATION",
                               message_id=card)
            snapshot = deepcopy(core.draft)
            for index, callback in enumerate((
                "case:v2:multi:clinicActions:-INVITED_FOR_EXAMINATION",
                "case:v2:multi:situationAreas:+TREATMENT",
                "case:v2:multi:clinicActions:DONE",
            )):
                await dispatch_raw(app, content=callback, message_id=card, update_id=5000 + index)
                assert core.draft == snapshot
            assert card not in deleted

    asyncio.run(run())
