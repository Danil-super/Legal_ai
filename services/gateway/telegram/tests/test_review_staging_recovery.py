import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.understanding_intent import bound_callback
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
            await dispatch("/menu")
            assert "understanding_intake" not in app.user_data[ACTOR]
            assert "answer_interpretation" not in app.user_data[ACTOR]
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


@pytest.mark.parametrize("action", ["command", "menu", "manual"])
@pytest.mark.parametrize("failure", ["current_pending", "discovery_failed", "access_denied"])
def test_review_superseded_fallback_never_clears_without_current_owned_discovery(action, failure):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            request = core._json_request

            async def unavailable_operation(method, path, **kwargs):
                if method == "GET" and (
                    path == f"/v1/intake-preparations/{operation}"
                    or (failure == "discovery_failed" and path.endswith("/understanding"))
                ):
                    raise LegalCoreApiError(503, "UNAVAILABLE", "synthetic failure")
                return await request(method, path, **kwargs)

            core._json_request = unavailable_operation
            core.deny = failure == "access_denied"
            selector = "/menu" if action == "command" else bound_callback(action, operation, DRAFT)
            await dispatch(selector)
            assert "understanding_intake" in app.user_data[ACTOR]
            assert core.starts == [operation] and not core.cancels and not core.stages
            assert_no_intermediate_consent_or_report(core, sent)
    asyncio.run(scenario())
