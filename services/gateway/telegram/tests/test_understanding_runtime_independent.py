"""Independent malformed accepted-receipt consumer boundary; no real network."""

import asyncio

from telegram_gateway.understanding_intent import bound_callback
from test_confirmed_understanding_runtime import TEXT, active_harness
from test_dialog_isolation import DRAFT


def test_accepted_receipt_cannot_claim_a_revision_newer_than_the_loaded_draft():
    async def scenario():
        async with active_harness() as (_app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            core.operations[operation]["status"].update(
                state="ACCEPTED", suggestions=None, source=None,
                acceptance={"draftId": str(DRAFT), "acceptedRevision": 999},
            )
            sent.clear()
            await dispatch(bound_callback("accept", operation, DRAFT))
            assert not any("Сведения сохранены" in item.get("text", "") for item in sent)
            assert not core.accepts
    asyncio.run(scenario())
