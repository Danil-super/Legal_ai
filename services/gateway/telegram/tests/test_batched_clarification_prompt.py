"""Real PTB prompts collect missing factual targets together before final consent."""

import asyncio
from copy import deepcopy
from uuid import UUID

import pytest
from telegram.ext import CallbackQueryHandler, ConversationHandler
from telegram_gateway import bot
from telegram_gateway.guided_case_intake_v2 import clarification_question, clarification_requests
from test_dialog_isolation import ACTOR, DRAFT
from test_intake_clarification_preflight import complete_treatment_input
from test_unified_intake_scenarios import versioned_harness


@pytest.mark.parametrize("services,long_names", [(1, False), (5, False), (5, True)])
def test_actual_ptb_batch_shows_all_missing_targets_with_first_button_fallback(
    services, long_names,
):
    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            data = complete_treatment_input()
            data["affectedServices"] = [
                str(slot) + "🦷" * 119 if long_names else f"Синтетическая услуга {slot}"
                for slot in range(services)
            ]
            core.draft = {"id": str(DRAFT), "revision": 7,
                          "wizardState": "ANALYSIS_CLARIFICATION", "draftData": data}
            await dispatch(f"case:draft:{DRAFT}")
            prompt = sent[-1]
            for request in clarification_requests(data):
                assert clarification_question(request, data) in prompt["text"]
            assert "одним сообщением" in prompt["text"]
            assert len(prompt["text"].encode("utf-16-le")) // 2 <= 4096
            buttons = {button.callback_data for row in prompt["reply_markup"].inline_keyboard
                       for button in row}
            assert "case:v2:clarify:SERVICE_PAYMENT_BASIS:0:PAID" in buttons
            assert not any(":1:" in callback for callback in buttons)
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == (
                "ANALYSIS_CLARIFICATION"
            )
            assert not core.submissions
    asyncio.run(scenario())


def test_actual_ptb_batch_does_not_repeat_explicit_unknown_and_shows_remaining_targets():
    async def scenario():
        async with versioned_harness() as (_app, core, sent, dispatch):
            data = complete_treatment_input()
            core.draft = {"id": str(DRAFT), "revision": 7,
                          "wizardState": "ANALYSIS_CLARIFICATION", "draftData": data}
            await dispatch(f"case:draft:{DRAFT}")
            payment = clarification_question(clarification_requests(data)[0], data)
            await dispatch("case:v2:clarify:SERVICE_PAYMENT_BASIS:0:UNKNOWN")
            prompt = sent[-1]["text"]
            assert payment not in prompt
            assert "Когда была оказана услуга" in prompt
            assert "Что клинике нужно сделать" in prompt
            assert core.draft["draftData"]["analysisClarificationAnswers"]["answers"][0][
                "value"
            ] == "UNKNOWN"
            assert not core.submissions
    asyncio.run(scenario())


@pytest.mark.parametrize("deny", [False, True])
def test_actual_ptb_direct_resume_uses_actual_callback_and_rechecks_exact_owned_draft(deny):
    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            core.draft = {"id": str(DRAFT), "revision": 7, "wizardState": "SUMMARY",
                          "draftData": complete_treatment_input()}
            before = deepcopy(core.draft)
            app.bot_data["intake_simplified_dialogue"] = True
            core.deny = deny
            conversation = next(handler for handlers in app.handlers.values()
                                for handler in handlers
                                if isinstance(handler, ConversationHandler) and handler.name == (
                                    "administrator-case-intake-v1"))

            async def resume(update, context):
                return await bot.resume_intake_draft(update, context, draft_id=DRAFT,
                                                     preparation=True)

            # Production recovery will move this callback into the conversation.
            # Isolate the shared resume API from the former group -2 installer.
            app.handlers[-2].clear()
            conversation.entry_points.insert(0, CallbackQueryHandler(
                resume, pattern=r"^case:clarify2:a{43}$",
            ))
            await dispatch("case:clarify2:" + "a" * 43)
            assert core.draft == before
            if deny:
                assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
                assert not any(before["draftData"]["eventSummary"] in message.get("text", "")
                               for message in sent)
                assert "недоступен" in sent[-1]["text"]
            else:
                data = app.user_data[ACTOR][bot.WIZARD_DATA_KEY]
                assert UUID(data[bot.DRAFT_ID_KEY]) == DRAFT
                assert data[bot.DRAFT_REVISION_KEY] == 7
                assert data[bot.DRAFT_STATE_KEY] == "ANALYSIS_CLARIFICATION"
                assert len(sent) == 1
                assert "Черновик открыт" not in sent[-1]["text"]
            assert not core.submissions
    asyncio.run(scenario())
