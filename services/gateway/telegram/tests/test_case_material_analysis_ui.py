"""Only an informed, delivered file prompt can opt an upload into analysis."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx2
import pytest
from telegram.error import TimedOut
from telegram_gateway import bot, case_material_usage
from telegram_gateway.case_wizard import LegalCoreClient
from test_dialog_isolation import ACTOR, DRAFT
from test_guided_case_intake_v2 import _base_data
from test_scenario_gateway_audit_20261014 import dispatch_raw
from test_unified_intake_scenarios import versioned_harness


def setup_upload(*, allowed=False):
    data = {"intakeVersion": 2, bot.DRAFT_ID_KEY: str(DRAFT),
            bot.DRAFT_REVISION_KEY: 7, bot.DRAFT_STATE_KEY: "MATERIALS"}
    reply = AsyncMock(return_value=SimpleNamespace(message_id=11))
    message = SimpleNamespace(reply_text=reply, message_id=12,
        from_user=SimpleNamespace(id=ACTOR, is_bot=False),
        chat=SimpleNamespace(id=ACTOR, type="private"))
    update = SimpleNamespace(effective_user=SimpleNamespace(id=ACTOR),
                             effective_message=message, message=message)
    core = SimpleNamespace(upload_case_material=AsyncMock(return_value={
        "draftRevision": 8,
        "material": {"id": str(uuid4()), "analysisUseAllowed": allowed},
    }))
    context = SimpleNamespace(user_data={bot.WIZARD_DATA_KEY: data},
                              bot_data={bot.LEGAL_CORE_CLIENT_KEY: core})
    return data, update, context, core, reply


@pytest.mark.parametrize("opted_in", [False, True])
def test_case_upload_transport_preserves_default_and_sends_only_explicit_opt_in(opted_in):
    async def scenario():
        seen = []

        async def handle(request):
            seen.append(request)
            return httpx2.Response(201, json={"draftRevision": 2, "material": {"id": str(DRAFT)}})

        async with httpx2.AsyncClient(base_url="http://synthetic.invalid",
                                      transport=httpx2.MockTransport(handle)) as http:
            await LegalCoreClient(http).upload_case_material(
                DRAFT, ACTOR, content=b"Synthetic anonymized document.",
                source_filename="synthetic.txt", content_type="text/plain",
                **({"include_in_analysis": True} if opted_in else {}),
            )
        assert dict(seen[0].url.params) == ({"include_in_analysis": "true"} if opted_in else {})
        assert seen[0].headers["X-Telegram-User-Id"] == str(ACTOR)

    asyncio.run(scenario())


def test_new_file_prompt_explains_analysis_and_keeps_the_offer_out_of_facts(monkeypatch):
    monkeypatch.setenv("CASE_MATERIAL_CONTEXT_ENABLED", "1")

    async def scenario():
        data, update, _ctx, _core, reply = setup_upload()
        await bot._prompt_v2_draft(update, bot.WizardState.MATERIALS, data)
        text = reply.await_args.args[0]
        assert "фрагмент" in text.casefold() and "отчёт" in text.casefold()
        assert "Hermes" not in text and "юрист сможет" not in text
        assert data[case_material_usage.OFFER_KEY] == (str(DRAFT), 11)
        assert case_material_usage.OFFER_KEY not in bot._draft_payload(data)

    asyncio.run(scenario())


@pytest.mark.parametrize("offered", [False, True])
def test_flag_change_alone_does_not_opt_in_a_file_shown_under_the_old_promise(monkeypatch, offered):
    async def scenario():
        monkeypatch.setenv("CASE_MATERIAL_CONTEXT_ENABLED", "1" if offered else "0")
        data, update, context, core, _reply = setup_upload(allowed=offered)
        await bot._prompt_v2_draft(update, bot.WizardState.MATERIALS, data)
        monkeypatch.setenv("CASE_MATERIAL_CONTEXT_ENABLED", "1")
        with patch.object(bot, "_save_v2_material_state", new_callable=AsyncMock):
            await bot._upload_v2_material(update, context, content=b"Synthetic document.",
                source_filename="synthetic.txt", content_type="text/plain",
                source_status="FILE_ATTACHED")
        assert core.upload_case_material.await_args.kwargs.get("include_in_analysis", False) \
            is offered
        assert case_material_usage.OFFER_KEY not in bot._draft_payload(data)

    asyncio.run(scenario())


def test_a_failed_prompt_cannot_record_an_analysis_offer(monkeypatch):
    monkeypatch.setenv("CASE_MATERIAL_CONTEXT_ENABLED", "1")

    async def scenario():
        data, update, _context, _core, reply = setup_upload()
        data[case_material_usage.OFFER_KEY] = (str(DRAFT), 10)
        reply.side_effect = TimedOut()
        with pytest.raises(TimedOut):
            await bot._prompt_v2_draft(update, bot.WizardState.MATERIALS, data)
        assert case_material_usage.OFFER_KEY not in data

    asyncio.run(scenario())


def test_unconfirmed_opt_in_receipt_never_claims_that_the_file_will_be_analyzed(monkeypatch):
    monkeypatch.setenv("CASE_MATERIAL_CONTEXT_ENABLED", "1")

    async def scenario():
        data, update, context, _core, reply = setup_upload(allowed=False)
        await bot._prompt_v2_draft(update, bot.WizardState.MATERIALS, data)
        with patch.object(bot, "_reconcile_v2_material", new_callable=AsyncMock,
                          return_value=bot.WizardState.MATERIALS) as reconcile, patch.object(
            bot, "_save_v2_material_state", new_callable=AsyncMock,
        ):
            await bot._upload_v2_material(update, context, content=b"Synthetic document.",
                source_filename="synthetic.txt", content_type="text/plain",
                source_status="FILE_ATTACHED")
        reconcile.assert_awaited_once()
        assert not reconcile.await_args.kwargs["receipt_confirmed"]
        assert not any("будет учтён" in str(c.args) for c in reply.await_args_list)

    asyncio.run(scenario())


def test_an_arriving_file_cannot_receive_consent_from_a_notice_sent_after_it(monkeypatch):
    monkeypatch.setenv("CASE_MATERIAL_CONTEXT_ENABLED", "1")

    async def scenario():
        data, update, context, core, _reply = setup_upload()
        core.get_intake_draft = AsyncMock(return_value={
            "id": str(DRAFT), "revision": 8, "wizardState": "MATERIALS",
            "draftData": {"intakeVersion": 2},
        })
        assert await bot._prepare_v2_material_input(update, context) == bot.WizardState.MATERIALS
        core.upload_case_material.assert_not_awaited()
        assert data[case_material_usage.OFFER_KEY] == (str(DRAFT), 11)

    asyncio.run(scenario())


def test_actual_telegram_dialogue_opts_in_only_after_the_delivered_file_notice(monkeypatch):
    monkeypatch.setenv("CASE_MATERIAL_CONTEXT_ENABLED", "1")

    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            core.draft = {"id": str(DRAFT), "revision": 7, "wizardState": "MATERIALS",
                          "draftData": _base_data()}
            core.submit_workflow = AsyncMock(side_effect=AssertionError("no implicit case consent"))
            await dispatch(f"case:draft:{DRAFT}")
            assert "фрагмент" in sent[-1]["text"].casefold()
            calls = []

            async def receive(request):
                calls.append(request)
                assert request.url.params["include_in_analysis"] == "true"
                core.draft["revision"] += 1
                core.draft["draftData"]["caseMaterialsStatus"] = "ATTACHED"
                return httpx2.Response(201, json={"draftRevision": core.draft["revision"],
                    "material": {"id": str(uuid4()), "analysisUseAllowed": True}})

            async with httpx2.AsyncClient(base_url="http://synthetic.invalid",
                                          transport=httpx2.MockTransport(receive)) as http:
                core.upload_case_material = LegalCoreClient(http).upload_case_material
                notice_id = app.user_data[ACTOR][bot.WIZARD_DATA_KEY]["_v2_prompt_message_id"]
                await dispatch_raw(app,
                    media={"text": "Синтетический обезличенный документ по ситуации."},
                    message_id=notice_id + 1, update_id=10000)
            assert len(calls) == 1
            assert case_material_usage.OFFER_KEY not in core.draft["draftData"]
            assert any("будут учтены" in message.get("text", "") for message in sent)
            core.submit_workflow.assert_not_awaited()
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "MATERIALS"

    asyncio.run(scenario())


@pytest.mark.parametrize("incoming_id", [None, 10, 11])
def test_a_queued_or_unidentified_upload_cannot_use_a_later_purpose_notice(
    monkeypatch, incoming_id,
):
    monkeypatch.setenv("CASE_MATERIAL_CONTEXT_ENABLED", "1")

    async def scenario():
        data, update, context, core, _reply = setup_upload(allowed=False)
        await bot._prompt_v2_draft(update, bot.WizardState.MATERIALS, data)
        update.message.message_id = incoming_id
        with patch.object(bot, "_save_v2_material_state", new_callable=AsyncMock), patch.object(
            bot, "_reconcile_v2_material", new_callable=AsyncMock,
            return_value=bot.WizardState.MATERIALS,
        ):
            await bot._upload_v2_material(update, context, content=b"Synthetic document.",
                source_filename="synthetic.txt", content_type="text/plain",
                source_status="FILE_ATTACHED")
        assert core.upload_case_material.await_args.kwargs.get("include_in_analysis", False) \
            is False

    asyncio.run(scenario())
