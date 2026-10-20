"""Clear waiting and draft selection in the actual simplified PTB composition."""

import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from telegram_gateway.understanding_intent import bound_callback
from test_confirmed_understanding_runtime import TEXT
from test_dialog_isolation import ACTOR, DRAFT
from test_intake_single_summary import simplified_harness


@pytest.mark.parametrize("initial", [True, False], ids=["description", "followup"])
def test_pending_message_explicitly_says_wait_without_manual_branch(initial):
    async def scenario():
        async with simplified_harness() as (_app, _core, sent, dispatch):
            await dispatch("quick:start" if initial else f"case:draft:{DRAFT}")
            await dispatch("text:" + TEXT)
            pending = sent[-1]
            assert "Ничего нажимать не нужно" in pending["text"]
            assert "автоматически" in pending["text"]
            labels = [button.text for row in pending["reply_markup"].inline_keyboard
                      for button in row]
            assert labels == ["Отменить и выйти в меню"]
    asyncio.run(scenario())


def test_old_manual_button_does_not_switch_or_cancel_another_active_case():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            other = uuid4()
            app.user_data[ACTOR]["understanding_intake"] = {
                "draftId": str(other), "operationId": str(uuid4()),
            }
            await dispatch(bound_callback("manual", operation, DRAFT))
            assert not core.cancels
            assert app.user_data[ACTOR]["understanding_intake"]["draftId"] == str(other)
            assert "другой черновик" in sent[-1]["text"]
    asyncio.run(scenario())


@pytest.mark.parametrize("pending", [True, False], ids=["understanding", "question"])
def test_old_draft_button_does_not_silently_replace_active_case(pending):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            if pending:
                await dispatch("quick:start")
                await dispatch("text:" + TEXT)
            else:
                await dispatch(f"case:draft:{DRAFT}")
            before = deepcopy(dict(app.user_data[ACTOR]))
            request = core.get_intake_draft
            other = uuid4()
            reads = []

            async def read(draft, actor):
                reads.append(draft)
                if draft == other:
                    return {"id": str(other), "revision": 1, "wizardState": "INCOMING",
                            "draftData": {"intakeVersion": 2}}
                return await request(draft, actor)

            core.get_intake_draft = read
            await dispatch(f"case:draft:{other}")

            assert other not in reads, "a stale resume button must not select another draft"
            assert dict(app.user_data[ACTOR]) == before
            assert "другой черновик" in sent[-1]["text"]
            assert not core.cancels and not core.stages and not core.jobs
    asyncio.run(scenario())
