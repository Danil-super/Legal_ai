"""New quick intake must reuse the same confirmed V2 facts as the guided route."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import ApplicationHandlerStop
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.clarification_runtime import create_clarification
from test_case_experience import harness
from test_dialog_isolation import ACTOR

DESCRIPTION = "Синтетический посетитель сообщил про долгое ожидание приёма в клинике."


def test_quick_proposal_uses_v2_and_does_not_infer_missing_answers():
    async def scenario():
        async with harness() as (app, _core, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + DESCRIPTION)
            pending = app.user_data[ACTOR]["quick_intake_candidate"]
            assert pending["candidate_data"] == {
                "intakeVersion": 2, "eventSummary": DESCRIPTION,
            }

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["create", "save"])
def test_mismatched_server_version_never_announces_confirmed_v2_save(stage):
    async def scenario():
        async with harness() as (app, core, sent, dispatch):
            original = core._json_request

            async def mismatch(method, path, **kwargs):
                result = await original(method, path, **kwargs)
                if (stage == "create" and method == "POST") or (
                    stage == "save" and method == "PUT"
                ):
                    result = deepcopy(result)
                    result["draftData"] = {"problem_summary": DESCRIPTION}
                return result

            core._json_request = mismatch
            await dispatch("quick:start")
            await dispatch("text:" + DESCRIPTION)
            pending = app.user_data[ACTOR]["quick_intake_candidate"]
            await dispatch(f"quick:review:{pending['nonce']}:accept")
            assert "quick_intake_candidate" in app.user_data[ACTOR]
            assert not any("Подтверждённые сведения сохранены" in item.get("text", "")
                           for item in sent)
            if stage == "create":
                assert not core.saved

    asyncio.run(scenario())


def test_quick_confirmation_explicitly_creates_v2_and_preserves_narrative():
    async def scenario():
        async with harness() as (app, core, _sent, dispatch):
            payloads = []
            original = core._json_request

            async def capture(method, path, **kwargs):
                payloads.append((method, kwargs["payload"]))
                return await original(method, path, **kwargs)

            core._json_request = capture
            await dispatch("quick:start")
            await dispatch("text:" + DESCRIPTION)
            pending = app.user_data[ACTOR]["quick_intake_candidate"]
            await dispatch(f"quick:review:{pending['nonce']}:accept")
            assert payloads[0] == ("POST", {"intakeSchemaVersion": "dental-case-intake.v2"})
            assert core.draft["draftData"] == {"intakeVersion": 2, "eventSummary": DESCRIPTION}
            assert core.draft["wizardState"] == "INCOMING"

    asyncio.run(scenario())


def test_unsupported_legacy_clarification_offers_new_guided_case_not_retry_loop():
    async def scenario():
        message = SimpleNamespace(message_id=77, reply_text=AsyncMock())
        case = "00000000-0000-4000-8000-000000000091"
        client = SimpleNamespace(clarification_preview=AsyncMock(side_effect=LegalCoreApiError(
            409, "CLARIFICATION_SCHEMA_UNSUPPORTED", "private synthetic diagnostic",
        )))
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=ACTOR), effective_message=message,
            callback_query=SimpleNamespace(data=f"case:clarify:{case}",
                                           answer=AsyncMock(), message=message),
        )
        context = SimpleNamespace(user_data={}, bot_data={bot.LEGAL_CORE_CLIENT_KEY: client})
        with pytest.raises(ApplicationHandlerStop):
            await create_clarification(update, context)
        text = message.reply_text.await_args.args[0]
        assert "стар" in text.lower() and "нов" in text.lower()
        assert "повторное нажатие" not in text.lower()
        assert "private synthetic diagnostic" not in text
        markup = message.reply_text.await_args.kwargs["reply_markup"]
        assert "case:start" in [button.callback_data
                                for row in markup.inline_keyboard for button in row]

    asyncio.run(scenario())
