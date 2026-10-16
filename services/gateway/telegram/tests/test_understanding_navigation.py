"""Active controls must survive restart, uncertain responses and ordinary navigation."""

import asyncio
from types import SimpleNamespace

import pytest
from telegram_gateway import bot
from telegram_gateway.understanding_intent import bound_callback
from test_confirmed_understanding_runtime import TEXT, active_harness
from test_dialog_isolation import ACTOR, DRAFT


@pytest.mark.parametrize("navigation", ["menu", "/menu", "quick:cancel", "case:start"])
def test_navigation_fences_pending_operation_before_changing_flow(navigation):
    async def scenario():
        async with active_harness() as (app, core, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            await dispatch(navigation)
            assert core.cancels == [operation]
            assert "understanding_intake" not in app.user_data[ACTOR]
    asyncio.run(scenario())


def test_unknown_cancel_outcome_never_claims_success_and_refresh_recovers():
    async def scenario():
        async with active_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            core.lose_cancel = True
            await dispatch(bound_callback("cancel", operation, DRAFT))
            assert "остановлен" not in sent[-1].get("text", "").lower()
            assert "understanding_intake" in app.user_data[ACTOR]
            await dispatch(bound_callback("refresh", operation, DRAFT))
            assert "understanding_intake" not in app.user_data[ACTOR]
            assert core.draft["draftData"]["eventSummary"] == TEXT
    asyncio.run(scenario())


def test_pending_card_menu_fences_after_complete_ram_loss():
    async def scenario():
        async with active_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            value = next(button.callback_data for row in sent[-1]["reply_markup"].inline_keyboard
                         for button in row if "Главное меню" in button.text)
            app.user_data[ACTOR].clear()
            await dispatch(value)
            assert core.cancels == [operation]
            assert "understanding_intake" not in app.user_data[ACTOR]
    asyncio.run(scenario())


def test_lost_acceptance_response_replays_from_durable_status_without_second_mutation():
    async def scenario():
        async with active_harness() as (app, core, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            core.propose(incomingKind="COMPLAINT")
            core.lose_accept = True
            value = bound_callback("accept", operation, DRAFT)
            await dispatch(value)
            app.user_data[ACTOR].clear()
            await dispatch(value)
            assert core.accepts == [operation]
            assert core.draft["revision"] == 3
            assert core.draft["draftData"]["incomingKind"] == "COMPLAINT"
    asyncio.run(scenario())


def test_new_application_discovers_pending_draft_then_manual_continues_without_dispatch():
    async def scenario():
        async with active_harness() as (_app, saved, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = saved.starts[-1]
        async with active_harness() as (app, _new, sent, dispatch):
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = saved
            await dispatch(f"case:draft:{DRAFT}")
            assert "Проверить результат" in str(sent[-1]["reply_markup"])
            assert len(saved.starts) == 1
            await dispatch(bound_callback("manual", operation, DRAFT))
            assert saved.cancels == [operation]
            await dispatch(f"case:draft:{DRAFT}")
            assert bot.WIZARD_DATA_KEY in app.user_data[ACTOR]
    asyncio.run(scenario())


def test_edit_navigation_cannot_leave_an_old_draft_selector_for_new_quick_entry():
    async def scenario():
        async with active_harness() as (app, core, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            await dispatch(bound_callback("edit", operation, DRAFT))
            assert app.user_data[ACTOR]["understanding_edit_draft"] == str(DRAFT)
            await dispatch("menu")
            assert "understanding_edit_draft" not in app.user_data[ACTOR]
    asyncio.run(scenario())


def test_foreign_actor_or_mismatched_draft_callback_cannot_confirm_or_cancel():
    async def scenario():
        async with active_harness() as (_app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            core.propose(incomingKind="COMPLAINT")
            await dispatch(bound_callback("accept", operation, DRAFT), actor=ACTOR + 1)
            await dispatch(bound_callback("cancel", operation, operation))
            assert not core.accepts and not core.cancels
            assert "Описание синтет" not in sent[-1].get("text", "")
    asyncio.run(scenario())


def test_real_actor_processor_accepts_manual_while_synthetic_model_is_still_blocked():
    async def scenario():
        async with active_harness() as (app, core, _sent, dispatch):
            actor_update = SimpleNamespace(effective_user=SimpleNamespace(id=ACTOR))
            processor = app.update_processor

            async def process(value):
                await asyncio.wait_for(processor.process_update(actor_update, dispatch(value)), 1)

            await process("quick:start")
            await process("text:" + TEXT)
            operation = core.starts[-1]
            release = asyncio.Event()

            async def delayed_model():
                await release.wait()
                core.propose(incomingKind="COMPLAINT")

            task = asyncio.create_task(delayed_model())
            await process(bound_callback("manual", operation, DRAFT))
            assert not release.is_set() and not task.done()
            assert core.cancels == [operation]
            release.set()
            await task
            assert "incomingKind" not in core.draft["draftData"]
    asyncio.run(scenario())
