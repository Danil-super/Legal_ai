import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from test_answer_runtime import AnswerCore
from test_confirmed_understanding_runtime import TEXT
from test_dialog_isolation import ACTOR, DRAFT
from test_initial_preparation_runtime import _context
from test_intake_single_summary import (
    assert_no_intermediate_consent_or_report,
    simplified_harness,
)


@pytest.mark.parametrize("initial", [True, False], ids=["description", "followup"])
def test_review_current_draft_recovers_both_lost_staging_replies_after_ram_restart(initial):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start" if initial else f"case:draft:{DRAFT}")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            if initial:
                core.propose(incomingKind="COMPLAINT")
            else:
                AnswerCore.propose(core, "COMPLAINT")
            request = core._json_request
            lost = 0

            async def lost_post_reply(method, path, **kwargs):
                nonlocal lost
                result = await request(method, path, **kwargs)
                if method == "POST" and path.endswith("/draft-staging"):
                    lost += 1
                    raise LegalCoreApiError(503, "UNAVAILABLE", "synthetic lost reply")
                return result

            core._json_request = lost_post_reply

            async def exhausted():
                while lost < 2 or app.bot_data.get("intake_delivery_tasks"):
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(exhausted(), 2)
            assert core.draft["draftData"]["incomingKind"] == "COMPLAINT"
            assert list(core.stages) == [operation]
            app.user_data[ACTOR].clear()
            await dispatch(f"case:draft:{DRAFT}")
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SITUATION"
            assert core.starts == [operation] and lost == 2
            assert_no_intermediate_consent_or_report(core, sent)
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["old_message", "new_revision", "other_draft", "denied"])
def test_review_summary_submission_preserves_authoritative_draft_and_source_guards(change):
    update, context, core, _message, _workflow = _context()
    context.bot_data["intake_simplified_dialogue"] = True
    update.callback_query.data = "case:v2:summary:confirm"
    if change == "old_message":
        update.callback_query.message.message_id += 1
    elif change == "new_revision":
        core.saved_draft["revision"] += 1
    elif change == "other_draft":
        core.saved_draft["id"] = str(uuid4())
    else:
        core.get_intake_draft = AsyncMock(
            side_effect=LegalCoreApiError(403, "DENIED", "synthetic denial"),
        )
    with patch(
        "telegram_gateway.dialogue_submission.enqueue_analysis", new_callable=AsyncMock,
    ) as enqueue:
        asyncio.run(bot._persisted(bot.confirm_case)(update, context))
    core.submit_workflow.assert_not_awaited()
    enqueue.assert_not_awaited()
