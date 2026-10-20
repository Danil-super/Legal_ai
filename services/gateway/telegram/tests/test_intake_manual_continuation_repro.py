"""Negative-path PTB reproduction; descriptions are fixed fictional examples."""

import asyncio

from telegram_gateway import bot
from telegram_gateway.understanding_intent import bound_callback
from test_confirmed_understanding_runtime import TEXT
from test_dialog_isolation import ACTOR, DRAFT
from test_intake_single_summary import await_state, simplified_harness


def test_manual_quick_continuation_opens_missing_question_without_resume_menu():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            before_manual = len(sent)

            await dispatch(bound_callback("manual", operation, DRAFT))

            assert core.cancels == [operation]
            assert core.draft["draftData"]["eventSummary"] == TEXT
            assert app.user_data[ACTOR].get(bot.WIZARD_DATA_KEY, {}).get(
                bot.DRAFT_STATE_KEY,
            ) == "INCOMING", "manual fallback must ask the missing question immediately"
            assert not any("Разбор остановлен" in item.get("text", "")
                           for item in sent[before_manual:])
            assert not core.jobs and not core.accepts
    asyncio.run(scenario())


def test_manual_quick_saved_source_survives_resume_and_non_treatment_selection():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            await dispatch(bound_callback("manual", operation, DRAFT))
            # Recover the durable draft as if the process lost its actor RAM.
            app.user_data[ACTOR].clear()
            await dispatch(f"case:draft:{DRAFT}")
            before_questions = len(sent)

            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            await dispatch("case:v2:multi:situationAreas:SERVICE")
            await dispatch("case:v2:multi:situationAreas:DONE")

            assert core.draft["draftData"]["eventSummary"] == TEXT
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "EVENT_DATE"
            assert not any("Кратко опишите, что произошло" in item.get("text", "")
                           for item in sent[before_questions:])
            assert core.starts == [operation] and not core.jobs and not core.accepts
    asyncio.run(scenario())


def test_failed_initial_interpretation_automatically_asks_only_missing_facts():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            before_failure = len(sent)
            # Public Core failure contract. No manual click or Telegram refresh.
            core.operations[operation]["status"].update(
                state="MANUAL_CONTINUATION", suggestions=None, source=None,
            )

            await await_state(app, "INCOMING")
            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            await dispatch("case:v2:multi:situationAreas:SERVICE")
            await dispatch("case:v2:multi:situationAreas:DONE")

            assert core.draft["draftData"]["eventSummary"] == TEXT
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "EVENT_DATE"
            assert not any("Кратко опишите, что произошло" in item.get("text", "")
                           for item in sent[before_failure:])
            assert core.starts == [operation] and not core.cancels and not core.jobs
    asyncio.run(scenario())


def test_guided_resume_skips_completed_event_checkpoint_without_initial_model_start():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            # A guided draft may retain an explicit navigation checkpoint even
            # though its narrative is already saved. Generic resume is not edit.
            core.draft.update(
                wizardState="EVENT", draftData={
                    "intakeVersion": 2, "eventSummary": TEXT,
                    "incomingKind": "COMPLAINT", "incomingSourceStatus": "NOT_ATTACHED",
                    "situationAreas": ["SERVICE"],
                },
            )
            await dispatch(f"case:draft:{DRAFT}")

            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "EVENT_DATE"
            assert core.draft["draftData"]["eventSummary"] == TEXT
            assert not any("Кратко опишите, что произошло" in item.get("text", "")
                           for item in sent)
            assert not core.starts and not core.jobs and not core.accepts
    asyncio.run(scenario())
