"""Independent checklist ack, current-card and reauthorization boundaries."""

import asyncio
from copy import deepcopy
from unittest.mock import patch

import pytest
from telegram.ext import ExtBot
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from test_answer_staged_recovery import amount_draft
from test_dialog_isolation import ACTOR
from test_intake_transient_cleanup import cleanup_harness, message_with
from test_scenario_gateway_audit_20261014 import dispatch_raw


@pytest.mark.parametrize("fault", ["state", "data", "revision", "revoked", "old-card"])
def test_checklist_does_not_render_or_delete_on_bad_ack_or_unauthorized_card(fault):
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         _events, _failures):
            fields = amount_draft()
            fields.pop("clinicActions")
            core.draft.update(wizardState="CLINIC_ACTIONS", draftData=fields)
            await dispatch("case:draft:" + core.draft["id"])
            card = message_with(visible, "Что клиника уже сделала")
            before = deepcopy(core.draft)
            save, transport = core.save_intake_draft, ExtBot._post
            edits, writes = [], []

            async def bad_ack(*args, **kwargs):
                writes.append(kwargs)
                receipt = await save(*args, **kwargs)
                if fault == "state":
                    receipt["wizardState"] = "HEALTH"
                elif fault == "data":
                    receipt["draftData"]["clinicActions"] = ["NOTHING_YET"]
                elif fault == "revision":
                    receipt["revision"] += 1
                return receipt

            async def denied_read(*_args, **_kwargs):
                raise LegalCoreApiError(403, "ACTOR_NOT_AUTHORIZED", "Synthetic revoked membership")

            async def record_edit(self, endpoint, data=None, **kwargs):
                if endpoint == "editMessageReplyMarkup":
                    edits.append(data)
                return await transport(self, endpoint, data=data, **kwargs)

            core.save_intake_draft = bad_ack
            if fault == "revoked":
                core.get_intake_draft = denied_read
            with patch.object(ExtBot, "_post", record_edit):
                await dispatch_raw(app, content="case:v2:multi:clinicActions:+CONTINUING_TREATMENT",
                                   message_id=card - 1 if fault == "old-card" else card)

            assert not edits and card not in deleted
            if fault in {"revoked", "old-card"}:
                assert not writes and core.draft == before
            if fault != "old-card":
                assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]

    asyncio.run(scenario())
