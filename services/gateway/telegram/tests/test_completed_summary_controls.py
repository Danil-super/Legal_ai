"""Real PTB dispatch retires completed recaps and recovers old optional controls."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from telegram.ext import ExtBot
from telegram_gateway import bot
from test_aif05_material_question_reproductions import known_data
from test_dialog_isolation import ACTOR
from test_initial_preparation_runtime import _context
from test_intake_single_summary import simplified_harness
from test_intake_transient_cleanup import delivery_finished
from test_scenario_gateway_audit_20261014 import dispatch_raw


async def open_summary(core, dispatch, app):
    await dispatch("quick:start")
    await dispatch("text:Учебная ситуация: пациент просит вернуть 5000 рублей.")
    proposed = known_data()
    for field in ("intakeVersion", "eventSummary", "incomingSourceStatus",
                  "caseMaterialsStatus", "conflictStage", "clinicActions"):
        proposed.pop(field)
    core.propose(**proposed)
    await delivery_finished(app)
    assert core.draft["wizardState"] == "SUMMARY"


def callbacks(message):
    markup = message.get("reply_markup")
    return ({button.callback_data for row in markup.inline_keyboard for button in row}
            if markup else set())


def test_actual_ptb_ordinary_summary_has_only_consent_and_material_controls():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await open_summary(core, dispatch, app)
            assert "case:v2:summary:confirm" in callbacks(sent[-1])
            assert not callbacks(sent[-1]) & {"case:v2:context:actions", "case:v2:context:history"}
    asyncio.run(scenario())


@pytest.mark.parametrize("legacy", ["actions", "history"])
def test_actual_ptb_completed_summary_is_retired_and_legacy_click_does_not_open_menu(legacy):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await open_summary(core, dispatch, app)
            summary = sent[-1]
            summary_id = len(sent) + 100
            receipt = _context()[2].submit_workflow.return_value
            core.submit_workflow = AsyncMock(return_value=receipt)
            core.archive_intake_draft = AsyncMock()
            answers = []
            post = ExtBot._post

            async def record(self, endpoint, data=None, **kwargs):
                if endpoint == "answerCallbackQuery":
                    answers.append(dict(data or {}))
                return await post(self, endpoint, data=data, **kwargs)

            with patch.object(ExtBot, "_post", record), patch(
                "telegram_gateway.dialogue_submission.enqueue_analysis", new_callable=AsyncMock,
            ):
                await dispatch("case:v2:summary:confirm")
                assert not callbacks(summary), "confirmed recap still offers actionable controls"
                count = len(sent)
                await dispatch_raw(app, content=f"case:v2:context:{legacy}", message_id=summary_id)
            assert not any(answer.get("show_alert") for answer in answers)
            assert any("завершена" in message.get("text", "") for message in sent[count:])
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            core.submit_workflow.assert_awaited_once()
    asyncio.run(scenario())


def test_actual_ptb_old_context_control_recovers_active_question_without_applying_answer():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await open_summary(core, dispatch, app)
            summary_id = len(sent) + 100
            await dispatch("case:v2:material:attach")
            before = deepcopy(core.draft)
            count = len(sent)
            answers = []
            post = ExtBot._post

            async def record(self, endpoint, data=None, **kwargs):
                if endpoint == "answerCallbackQuery":
                    answers.append(dict(data or {}))
                return await post(self, endpoint, data=data, **kwargs)

            with patch.object(ExtBot, "_post", record):
                await dispatch_raw(app, content="case:v2:context:actions", message_id=summary_id)
            assert core.draft == before
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "MATERIALS"
            assert any("case:v2:material:skip" in callbacks(message) for message in sent[count:])
            assert not any(answer.get("show_alert") for answer in answers)
            assert not core.accepts and not core.jobs
    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["healthDeteriorationReported", "representativeContact",
                                   "moneyRequested", "amount"])
def test_actual_ptb_explicit_unknown_survives_summary_and_submission_without_repeat(field):
    from telegram_gateway.factual_safety_intake import answer_screening
    from test_dialog_isolation import DRAFT
    from test_unified_intake_scenarios import versioned_harness

    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            data = known_data()
            if field == "amount":
                answer_screening(data, "moneyRequested", "YES")
            answer_screening(data, field, "UNKNOWN")
            screening = deepcopy(data["safetyScreening"])
            core.draft = {"id": str(DRAFT), "revision": 7, "wizardState": "SUMMARY",
                          "draftData": data}
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("case:v2:summary:confirm")
            assert core.draft["wizardState"] == "V2_CONFIRM"
            assert core.draft["draftData"]["safetyScreening"] == screening
            await dispatch(f"case:confirm:{DRAFT}")
            assert len(core.submissions) == 1
            fact = next(item for item in core.submissions[0][1]
                        if item["factKey"] == "FACTUAL_SAFETY_SCREENING")
            assert fact["value"] == screening
            assert core.draft["draftData"]["safetyScreening"] == screening
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "V2_CONFIRM"
    asyncio.run(scenario())


def test_actual_ptb_legacy_context_rechecks_access_before_rendering_cached_facts():
    from telegram_gateway.case_wizard import LegalCoreApiError

    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await open_summary(core, dispatch, app)
            summary_id = len(sent) + 100
            await dispatch("case:v2:material:attach")
            before = deepcopy(core.draft)
            count = len(sent)
            core.get_intake_draft = AsyncMock(side_effect=LegalCoreApiError(
                403, "ACTOR_NOT_AUTHORIZED", "synthetic revoked access",
            ))
            await dispatch_raw(app, content="case:v2:context:actions", message_id=summary_id)
            assert core.draft == before
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            assert not any(before["draftData"]["eventSummary"] in item.get("text", "")
                           for item in sent[count:])
            assert any("недоступен" in item.get("text", "") for item in sent[count:])
            assert not core.accepts and not core.jobs
    asyncio.run(scenario())


def test_actual_ptb_stale_context_on_summary_restores_current_controls_without_editing():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await open_summary(core, dispatch, app)
            before = deepcopy(core.draft)
            count = len(sent)
            await dispatch_raw(app, content="case:v2:context:actions", message_id=101)
            assert core.draft == before
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SUMMARY"
            assert any("case:v2:summary:confirm" in callbacks(item) for item in sent[count:])
            assert not core.accepts and not core.jobs
    asyncio.run(scenario())
