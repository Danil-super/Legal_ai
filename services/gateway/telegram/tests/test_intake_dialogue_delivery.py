"""User-visible delivery with real PTB handlers and synthetic network boundaries."""

import asyncio
from copy import deepcopy

import pytest
from telegram_gateway.answer_intent import bound_callback as answer_callback
from telegram_gateway.understanding_intent import bound_callback as initial_callback
from test_answer_runtime import answer_harness
from test_confirmed_understanding_runtime import TEXT, active_harness
from test_dialog_isolation import DRAFT


def has_action(message, callback):
    markup = message.get("reply_markup")
    return markup is not None and any(
        button.callback_data == callback
        for row in markup.inline_keyboard for button in row
    )


async def await_proposal(sent, callback):
    async def delivered():
        while not any(has_action(message, callback) for message in sent):
            await asyncio.sleep(0.001)

    await asyncio.wait_for(delivered(), timeout=2)


def test_initial_description_delivers_ready_proposal_without_refresh_or_restarting_model():
    async def scenario():
        async with active_harness() as (_app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            core.propose(incomingKind="COMPLAINT")

            # No further incoming Telegram update: completion must reach the user itself.
            await await_proposal(sent, initial_callback("accept", operation, DRAFT))

            assert core.starts == [operation]
            assert not core.accepts and not core.jobs
            assert "incomingKind" not in core.draft["draftData"]
    asyncio.run(scenario())


def test_followup_answer_delivers_ready_proposal_without_refresh_or_restarting_model():
    async def scenario():
        async with answer_harness() as (_app, core, sent, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:Пациент недоволен")
            operation = core.starts[-1]
            core.propose("COMPLAINT")

            await await_proposal(sent, answer_callback("accept", operation, DRAFT))

            assert core.starts == [operation]
            assert not core.accepts and not core.jobs
            assert "incomingKind" not in core.draft["draftData"]
    asyncio.run(scenario())


@pytest.mark.parametrize("initial", [True, False], ids=["description", "followup"])
def test_ready_proposal_has_only_confirm_edit_and_menu_actions(initial):
    async def scenario():
        factory = active_harness if initial else answer_harness
        callback = initial_callback if initial else answer_callback
        async with factory() as (_app, core, sent, dispatch):
            await dispatch("quick:start" if initial else f"case:draft:{DRAFT}")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            if initial:
                core.propose(incomingKind="COMPLAINT")
            else:
                core.propose("COMPLAINT")
            # Legacy refresh remains a supported recovery selector, not a visible ready action.
            await dispatch(callback("refresh", operation, DRAFT))

            actions = [button.callback_data
                       for row in sent[-1]["reply_markup"].inline_keyboard for button in row]
            assert actions == [callback(action, operation, DRAFT)
                               for action in ("accept", "edit", "menu")]
    asyncio.run(scenario())


def test_leaving_to_menu_fences_an_already_inflight_proposal_notification():
    async def scenario():
        async with active_harness() as (_app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            core.propose(incomingKind="COMPLAINT")
            reading, release = asyncio.Event(), asyncio.Event()
            request = core._json_request

            async def delayed_get(method, path, **kwargs):
                result = await request(method, path, **kwargs)
                if method == "GET" and path == f"/v1/intake-preparations/{operation}" and (
                    not reading.is_set()
                ):
                    snapshot = deepcopy(result)
                    reading.set()
                    await release.wait()
                    return snapshot
                return result

            core._json_request = delayed_get
            try:
                await asyncio.wait_for(reading.wait(), timeout=2)
                await dispatch("/menu")
                after_navigation = len(sent)
                release.set()
                await asyncio.sleep(0.05)

                assert core.cancels == [operation]
                assert not any(has_action(message, initial_callback("accept", operation, DRAFT))
                               for message in sent[after_navigation:])
                assert not core.accepts and not core.jobs
            finally:
                release.set()
    asyncio.run(scenario())
