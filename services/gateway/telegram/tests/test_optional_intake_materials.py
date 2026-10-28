"""Optional uploads do not gate facts or remove the existing upload workflow."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from legal_core.api_contracts import FactInput
from legal_core.case_api import _input_value
from legal_core.intake import missing_facts_for
from telegram.ext import ConversationHandler
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError, facts_from_v2_data
from telegram_gateway.dialogue_staging import ENABLED
from telegram_gateway.guided_case_intake_v2 import next_missing_state, review_blocks
from test_case_wizard import FakeQuery
from test_dialog_isolation import ACTOR, DRAFT
from test_factual_safety_gateway import completed_data
from test_initial_preparation_runtime import _context
from test_intake_single_summary import simplified_harness
from test_intake_transient_cleanup import delivery_finished


def complete_without_upload():
    data = completed_data()
    data.pop("caseMaterialsStatus")
    return data


def test_missing_optional_upload_opens_summary_without_fabricating_other_facts():
    data = complete_without_upload()
    original = deepcopy(data)
    assert next_missing_state(data) == "SUMMARY"
    assert data == original
    facts = {item.fact_key: _input_value(item.value_type, item.value)
             for item in map(FactInput.model_validate, facts_from_v2_data(data))}
    assert missing_facts_for(facts) == []
    assert "Материалы клиники: материалы клиники не приложены" in review_blocks(data)


def test_missing_materials_does_not_skip_safety_or_unknown_is_not_a_negative():
    data = complete_without_upload()
    data["safetyScreening"].pop("representativeContact")
    assert next_missing_state(data) == "SAFETY"
    assert "representativeContact" not in data["safetyScreening"]


def test_actual_dialogue_shows_summary_and_lets_user_choose_optional_upload_before_confirmation():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:Учебный пример: посетитель пожаловался на ожидание.")
            proposal = complete_without_upload()
            proposal.pop("intakeVersion")
            proposal.pop("incomingSourceStatus")
            proposal.pop("eventSummary")
            core.propose(**proposal)
            await delivery_finished(app)
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SUMMARY"
            assert not core.jobs and not core.accepts
            keyboard = sent[-1]["reply_markup"]
            buttons = {button.callback_data for row in keyboard.inline_keyboard for button in row}
            assert "case:v2:summary:confirm" in buttons
            assert "case:v2:material:attach" in buttons
            await dispatch("case:v2:material:attach")
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "MATERIALS"
            assert not core.jobs and not core.accepts
            await dispatch("case:v2:material:skip")
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SUMMARY"
            assert core.draft["draftData"]["caseMaterialsStatus"] == "NOT_ATTACHED"
    asyncio.run(scenario())


def test_explicit_material_step_survives_resume_in_simplified_dialogue():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            core.draft.update(wizardState="MATERIALS", draftData=complete_without_upload())
            await dispatch(f"case:draft:{DRAFT}")
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "MATERIALS"
            assert "Можно продолжить без файла" in sent[-1]["text"]
    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["NOT_ATTACHED", "ATTACHED"])
def test_summary_add_file_choice_survives_menu_and_resume_without_losing_attachments(status):
    async def scenario():
        async with simplified_harness() as (app, core, _sent, dispatch):
            data = completed_data() | {"caseMaterialsStatus": status}
            core.draft.update(wizardState="SUMMARY", draftData=data)
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("case:v2:material:attach")
            assert core.draft["wizardState"] == "MATERIALS"
            await dispatch("/menu")
            await dispatch(f"case:draft:{DRAFT}")
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "MATERIALS"
            if status == "ATTACHED":
                assert core.draft["draftData"]["caseMaterialsStatus"] == "ATTACHED"
    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["empty", "invalid", "unavailable", "forbidden"])
def test_attached_flag_without_confirmed_live_metadata_never_submits_case(fault):
    update, context, core, message, _ = _context()
    update.callback_query = FakeQuery("case:v2:summary:confirm")
    context.bot_data[ENABLED] = True
    context.user_data[bot.WIZARD_DATA_KEY]["caseMaterialsStatus"] = "ATTACHED"
    core._json_request = AsyncMock(return_value={"items": []} if fault == "empty" else {
        "items": [{"id": "invalid"}],
    })
    if fault in {"unavailable", "forbidden"}:
        core._json_request.side_effect = LegalCoreApiError(
            503 if fault == "unavailable" else 403, "UNAVAILABLE", "private detail",
        )
    with patch("telegram_gateway.dialogue_submission.enqueue_analysis", new_callable=AsyncMock):
        state = asyncio.run(bot.confirm_case(update, context))
    assert state == (bot.WizardState.MATERIALS if fault == "empty" else bot.WizardState.SUMMARY)
    core.submit_workflow.assert_not_awaited()
    assert "private detail" not in " ".join(message.text_replies)
    assert context.user_data[bot.WIZARD_DATA_KEY]["caseMaterialsStatus"] == "ATTACHED"
    if fault == "empty":
        callbacks = {button.callback_data for row in message.text_reply_markups[-1].inline_keyboard
                     for button in row}
        assert "case:v2:material:skip" in callbacks


def test_confirmed_attachment_stays_attached_and_is_not_sent_to_the_model():
    update, context, core, _message, workflow = _context()
    update.callback_query = FakeQuery("case:v2:summary:confirm")
    context.bot_data[ENABLED] = True
    context.user_data[bot.WIZARD_DATA_KEY]["caseMaterialsStatus"] = "ATTACHED"
    core._json_request = AsyncMock(return_value={"items": [{
        "id": str(UUID(int=765)), "displayName": "Материал 1", "mimeType": "text/plain",
        "sizeBytes": 20, "createdAt": "2026-10-27T00:00:00Z",
    }]})
    with patch("telegram_gateway.dialogue_submission.enqueue_analysis", new_callable=AsyncMock):
        state = asyncio.run(bot.confirm_case(update, context))
    assert state == ConversationHandler.END
    core._json_request.assert_awaited_once_with(
        "GET", f"/v1/telegram-intake-drafts/{workflow}/case-materials",
        telegram_user_id=7_000_000_001,
    )
    facts = {item["factKey"]: item["value"] for item in core.submit_workflow.await_args.args[1]}
    assert facts["CASE_MATERIALS_STATUS"] == {"value": "ATTACHED"}
    assert "Материал 1" not in str(core.submit_workflow.await_args)


@pytest.mark.parametrize("replay", [True, False])
def test_closed_material_draft_only_allows_retry_of_an_authorized_existing_workflow(replay):
    update, context, core, _message, _ = _context()
    update.callback_query = FakeQuery("case:v2:summary:confirm")
    context.bot_data[ENABLED] = True
    context.user_data[bot.WIZARD_DATA_KEY]["caseMaterialsStatus"] = "ATTACHED"
    core._json_request = AsyncMock(side_effect=LegalCoreApiError(
        404, "INTAKE_DRAFT_NOT_FOUND", "closed draft",
    ))
    core.get_workflow = AsyncMock(return_value=core.submit_workflow.return_value)
    if not replay:
        core.get_workflow.side_effect = LegalCoreApiError(404, "WORKFLOW_NOT_FOUND", "not saved")
    with patch("telegram_gateway.dialogue_submission.enqueue_analysis", new_callable=AsyncMock):
        state = asyncio.run(bot.confirm_case(update, context))
    if replay:
        assert state == ConversationHandler.END
        core.submit_workflow.assert_awaited_once()
    else:
        assert state == bot.WizardState.SUMMARY
        core.submit_workflow.assert_not_awaited()


@pytest.mark.parametrize("change", ["workflow", "receipt-version", "intake-schema", "state"])
def test_invalid_or_other_workflow_receipt_cannot_bypass_missing_material_metadata(change):
    update, context, core, _message, _ = _context()
    update.callback_query = FakeQuery("case:v2:summary:confirm")
    context.bot_data[ENABLED] = True
    context.user_data[bot.WIZARD_DATA_KEY]["caseMaterialsStatus"] = "ATTACHED"
    wire = deepcopy(core.submit_workflow.return_value)
    if change == "workflow":
        wire["workflowId"] = str(UUID(int=987))
    elif change == "receipt-version":
        wire["receiptVersion"] = True
    elif change == "intake-schema":
        wire["case"]["intakeSchemaVersion"] = "dental-case-intake.v1"
    else:
        wire["state"] = "PENDING"
    core._json_request = AsyncMock(side_effect=LegalCoreApiError(
        404, "INTAKE_DRAFT_NOT_FOUND", "closed draft",
    ))
    core.get_workflow = AsyncMock(return_value=wire)
    assert asyncio.run(bot.confirm_case(update, context)) == bot.WizardState.SUMMARY
    core.submit_workflow.assert_not_awaited()
