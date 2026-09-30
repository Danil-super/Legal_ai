"""Regression coverage for draft progress and competing Telegram input modes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from telegram.error import TimedOut
from telegram.ext import ApplicationHandlerStop, ConversationHandler
from telegram_gateway import bot
from telegram_gateway.analysis_runtime import open_escalation_discussion
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.quick_intake_runtime import start_quick_intake

DRAFT_ID = str(UUID(int=123))
ACTOR_ID = 777


def context_with_draft(state: bot.WizardState) -> SimpleNamespace:
    client = SimpleNamespace(
        save_intake_draft=AsyncMock(return_value={"revision": 8}),
        get_actor=AsyncMock(return_value={"role": "CLINIC_ADMIN"}),
        create_intake_draft=AsyncMock(
            return_value={"id": DRAFT_ID, "wizardState": "INCIDENT", "revision": 1}
        ),
    )
    return SimpleNamespace(
        bot_data={bot.LEGAL_CORE_CLIENT_KEY: client},
        user_data={
            bot.WIZARD_DATA_KEY: {
                "workflow_id": DRAFT_ID,
                bot.DRAFT_ID_KEY: DRAFT_ID,
                bot.DRAFT_REVISION_KEY: 7,
                bot.DRAFT_STATE_KEY: state.name,
                "response_deadline": {"date": "2026-08-25", "precision": "EXACT"},
            }
        },
    )


def fake_update(*, text: str = "", callback: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=ACTOR_ID),
        effective_message=SimpleNamespace(
            text=text, reply_text=AsyncMock(), reply_document=AsyncMock()
        ),
        callback_query=(
            None if callback is None else SimpleNamespace(data=callback, answer=AsyncMock())
        ),
    )


@pytest.mark.parametrize("answer", ["2026-08-30", "unknown"])
def test_deadline_transition_persists_progress_when_earliest_deadline_does_not_change(
    answer: str,
) -> None:
    context = context_with_draft(bot.WizardState.LAWYER_DEADLINE)
    update = fake_update(text=answer)

    state = asyncio.run(bot._persist_transition(bot.record_lawyer_deadline, update, context))

    assert state == bot.WizardState.AUTHORITY
    saved = context.bot_data[bot.LEGAL_CORE_CLIENT_KEY].save_intake_draft.call_args.kwargs
    assert saved["wizard_state"] == "AUTHORITY"
    assert saved["draft_data"] == {
        "response_deadline": {"date": "2026-08-25", "precision": "EXACT"}
    }
    assert context.user_data[bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "AUTHORITY"


def test_invalid_answer_does_not_write_a_draft_revision() -> None:
    context = context_with_draft(bot.WizardState.LAWYER_DEADLINE)
    state = asyncio.run(
        bot._persist_transition(bot.record_lawyer_deadline, fake_update(text="bad"), context)
    )
    assert state == bot.WizardState.LAWYER_DEADLINE
    context.bot_data[bot.LEGAL_CORE_CLIENT_KEY].save_intake_draft.assert_not_called()


def test_starting_case_disarms_old_inputs_that_would_consume_its_answers() -> None:
    context = context_with_draft(bot.WizardState.INCIDENT)
    context.user_data.update(
        {
            "escalation_discussion_id": DRAFT_ID,
            "quick_intake_pending": True,
            "clinic_document_effective_date_pending": {"document_key": "old"},
            bot.ADMIN_GRANT_ACCESS_KEY: True,
        }
    )
    state = asyncio.run(bot.case_start(fake_update(callback="case:start"), context))
    assert state == bot.WizardState.INCIDENT
    assert set(context.user_data) == {bot.WIZARD_DATA_KEY}


def test_starting_quick_intake_disarms_previous_discussion_and_access_prompts() -> None:
    context = context_with_draft(bot.WizardState.INCIDENT)
    context.user_data = {"escalation_discussion_id": DRAFT_ID, bot.ADMIN_GRANT_ACCESS_KEY: True}
    asyncio.run(start_quick_intake(fake_update(), context))
    assert context.user_data == {"quick_intake_pending": True}


def test_discussion_cannot_steal_answers_from_an_active_case() -> None:
    context = context_with_draft(bot.WizardState.SERVICE_TYPE)
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(
            open_escalation_discussion(
                fake_update(callback=f"case:escalation:{DRAFT_ID}"), context
            )
        )
    assert "escalation_discussion_id" not in context.user_data
    assert bot.WIZARD_DATA_KEY in context.user_data


def test_failed_draft_resume_clears_stale_selection_after_conversation_ends() -> None:
    context = context_with_draft(bot.WizardState.SERVICE_TYPE)
    context.bot_data[bot.LEGAL_CORE_CLIENT_KEY].get_intake_draft = AsyncMock(
        side_effect=LegalCoreApiError(404, "DRAFT_NOT_FOUND", "missing")
    )
    state = asyncio.run(
        bot.resume_intake_draft(fake_update(callback=f"case:draft:{DRAFT_ID}"), context)
    )
    assert state == ConversationHandler.END
    assert bot.WIZARD_DATA_KEY not in context.user_data


def test_menu_is_available_even_when_core_client_is_not_initialized() -> None:
    context = SimpleNamespace(bot_data={}, user_data={})
    keyboard = asyncio.run(bot._main_menu_for_actor(fake_update(), context))
    assert keyboard.inline_keyboard


def test_main_menu_loads_independent_actor_capabilities_in_parallel() -> None:
    async def scenario() -> None:
        actor_started = asyncio.Event()
        editor_started = asyncio.Event()
        release = asyncio.Event()

        class SlowCore:
            async def get_actor(self, actor_id: int) -> dict[str, str]:
                assert actor_id == ACTOR_ID
                actor_started.set()
                await release.wait()
                return {"role": "CLINIC_OWNER"}

            async def get_legal_editor_status(self, actor_id: int) -> dict[str, bool]:
                assert actor_id == ACTOR_ID
                editor_started.set()
                await release.wait()
                return {"isLegalEditor": True}

        context = SimpleNamespace(
            bot_data={bot.LEGAL_CORE_CLIENT_KEY: SlowCore()},
            user_data={},
        )
        pending = asyncio.create_task(bot._main_menu_for_actor(fake_update(), context))
        await asyncio.wait_for(asyncio.gather(actor_started.wait(), editor_started.wait()), 0.1)
        release.set()
        keyboard = await pending
        callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row}
        assert {"editor:open", "case:escalations"} <= callbacks

    asyncio.run(scenario())


def test_delivery_failure_keeps_confirmation_retryable_without_archiving_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = context_with_draft(bot.WizardState.CONFIRM)
    client = context.bot_data[bot.LEGAL_CORE_CLIENT_KEY]
    client.submit_workflow = AsyncMock(return_value={})
    client.archive_intake_draft = AsyncMock()
    monkeypatch.setattr(bot, "_draft_from_data", lambda data: object())
    monkeypatch.setattr(bot, "facts_from_draft", lambda draft: [])
    monkeypatch.setattr(bot, "_send_workflow_report", AsyncMock(side_effect=TimedOut()))
    update = fake_update(callback=f"case:confirm:{DRAFT_ID}")

    state = asyncio.run(bot.confirm_case(update, context))

    assert state == bot.WizardState.CONFIRM
    assert bot.WIZARD_DATA_KEY in context.user_data
    client.archive_intake_draft.assert_not_called()
    client.submit_workflow.assert_awaited_once_with(UUID(DRAFT_ID), [], ACTOR_ID)
    assert "Telegram" in update.effective_message.reply_text.call_args.args[0]


def test_long_analysis_preserves_the_entire_draft_and_review_warning() -> None:
    from telegram_gateway.analysis_runtime import telegram_analysis_messages

    draft = "synthetic response " * 150 + "🦷" * 700 + " final sentence"
    payload = {
        "analysisAllowed": True,
        "escalationRequired": False,
        "report": {
            "reportJson": {
                "case": {"publicNumber": "DL-2026-TEST"},
                "risk": {"level": "LOW", "reasonCodes": [], "escalationRequired": False},
                "recommendations": {"items": []},
                "legalBasis": {"sources": []},
                "draftResponse": {"status": "AVAILABLE", "text": draft},
            }
        },
    }
    messages = telegram_analysis_messages(payload)
    assert len(messages) > 1
    assert all(len(message.encode("utf-16-le")) // 2 <= 4_000 for message in messages)
    combined = "".join(messages)
    assert draft in combined
    assert "Перед отправкой текст должен проверить" in combined
    assert combined.endswith("Автоматическая отправка пациенту отключена.")


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel", None])
@pytest.mark.parametrize("callback", [None, f"case:confirm:{DRAFT_ID}"])
def test_shared_chat_updates_stop_before_case_handlers_and_do_not_reveal_data(
    chat_type: str | None, callback: str | None,
) -> None:
    update = fake_update(text="synthetic confidential case content", callback=callback)
    update.effective_chat = (
        None if chat_type is None else SimpleNamespace(type=chat_type)
    )
    context = context_with_draft(bot.WizardState.CONFIRM)
    original_data = repr(context.user_data)
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(bot._require_private_chat(update, context))
    assert repr(context.user_data) == original_data
    update.effective_message.reply_document.assert_not_called()
    if callback is None:
        notice = update.effective_message.reply_text.call_args.args[0]
    else:
        update.effective_message.reply_text.assert_not_called()
        notice = update.callback_query.answer.call_args.args[0]
    assert "synthetic confidential" not in notice
    assert DRAFT_ID not in notice


def test_private_chat_remains_available_to_existing_handlers() -> None:
    update = fake_update()
    update.effective_chat = SimpleNamespace(type="private")
    asyncio.run(bot._require_private_chat(update, context_with_draft(bot.WizardState.INCIDENT)))
    update.effective_message.reply_text.assert_not_called()


def test_private_chat_guard_precedes_all_feature_handlers() -> None:
    from telegram_gateway.legal_library_runtime import build_application_with_legal_library

    application = build_application_with_legal_library("123456:test_token")
    assert application.handlers[-99][0].callback is bot._require_private_chat
    assert all(
        group > -99
        for group in application.handlers
        if group not in {-100, -99}
    )


def test_uncertain_legal_date_explains_how_to_supply_a_corrected_case() -> None:
    from telegram_gateway.analysis_runtime import analysis_error_message

    message = analysis_error_message("ANALYSIS_DATE_UNCERTAIN")
    assert "точную дату" in message
    assert "Создайте новый кейс" in message


def test_durable_result_callback_delivers_every_chunk_to_the_private_actor() -> None:
    from telegram_gateway.analysis_jobs_runtime import JOBS_CLIENT_KEY, status_callback

    draft = "synthetic response " * 250 + " final sentence"
    payload = {
        "analysisAllowed": True,
        "escalationRequired": False,
        "report": {
            "reportJson": {
                "case": {"publicNumber": "DL-2026-TEST"},
                "risk": {"level": "LOW", "reasonCodes": [], "escalationRequired": False},
                "recommendations": {"items": []},
                "legalBasis": {"sources": []},
                "draftResponse": {"status": "AVAILABLE", "text": draft},
            }
        },
    }
    client = SimpleNamespace(status=AsyncMock(return_value={
        "jobId": DRAFT_ID,
        "caseId": DRAFT_ID,
        "state": "SUCCEEDED",
        "errorCode": None,
        "result": payload,
    }))
    context = SimpleNamespace(
        bot_data={JOBS_CLIENT_KEY: client}, bot=SimpleNamespace(send_message=AsyncMock())
    )
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(status_callback(fake_update(callback=f"analysis:result:{DRAFT_ID}"), context))
    sent = context.bot.send_message.call_args_list
    assert len(sent) > 1
    assert all(call.kwargs["chat_id"] == ACTOR_ID for call in sent)
    assert all(len(call.kwargs["text"].encode("utf-16-le")) // 2 <= 4_000 for call in sent)
    combined = "".join(call.kwargs["text"] for call in sent)
    assert draft in combined
    assert combined.endswith("Автоматическая отправка пациенту отключена.")
