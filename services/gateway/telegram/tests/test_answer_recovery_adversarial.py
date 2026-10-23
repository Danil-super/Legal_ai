"""Independent recovery boundary checks with the actual PTB composition."""

import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from telegram_gateway import bot
from telegram_gateway.answer_intent import bound_callback
from telegram_gateway.case_wizard import LegalCoreApiError
from test_answer_staged_recovery import enter_amount, fail_amount_operation
from test_dialog_isolation import ACTOR, DRAFT
from test_intake_single_summary import simplified_harness
from test_intake_transient_cleanup import delivery_finished


@pytest.mark.parametrize("failure", ["revoked", "unavailable", "wrong-draft"])
def test_recovery_does_not_reopen_facts_when_current_draft_reauthorization_fails(failure):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            operation = await enter_amount(app, core, dispatch)
            fail_amount_operation(core, operation)
            await delivery_finished(app)
            app.user_data[ACTOR].pop("answer_manual_once", None)
            snapshot, before = deepcopy(core.draft), len(sent)

            async def forbidden_read(*_args, **_kwargs):
                if failure == "wrong-draft":
                    return snapshot | {"id": str(uuid4())}
                raise LegalCoreApiError(403 if failure == "revoked" else 503,
                                        "ACTOR_NOT_AUTHORIZED", "Synthetic denial")

            core.get_intake_draft = forbidden_read
            await dispatch(bound_callback("manual", operation, DRAFT))

            new = sent[before:]
            assert not any("Какую сумму просит пациент" in item.get("text", "") for item in new)
            assert core.draft == snapshot and not core.stages and not core.cancels
            assert "answer_manual_once" not in app.user_data[ACTOR]
            assert any(button.callback_data == "menu"
                       for item in new if item.get("reply_markup")
                       for row in item["reply_markup"].inline_keyboard for button in row)

    asyncio.run(scenario())


@pytest.mark.parametrize("discovery", ["unavailable", "extra-field", "wrong-operation"])
def test_recovery_requires_successful_strict_empty_discovery_before_resuming(discovery):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            operation = await enter_amount(app, core, dispatch)
            fail_amount_operation(core, operation)
            await delivery_finished(app)
            app.user_data[ACTOR].pop("answer_manual_once", None)
            snapshot, before = deepcopy(core.draft), len(sent)
            request = core._json_request

            async def bad_discovery(method, path, **kwargs):
                if method == "GET" and path.endswith("/answer-interpretations"):
                    if discovery == "unavailable":
                        raise LegalCoreApiError(503, "UNAVAILABLE", "Synthetic transport fault")
                    if discovery == "extra-field":
                        return {"operation": None, "saved": True}
                    return {"operation": {"operationId": str(uuid4()), "state": "PENDING",
                                          "question": None, "interpretation": None,
                                          "source": None, "acceptance": None}}
                return await request(method, path, **kwargs)

            core._json_request = bad_discovery
            await dispatch(bound_callback("manual", operation, DRAFT))

            assert not any("Какую сумму просит пациент" in item.get("text", "")
                           for item in sent[before:])
            assert core.draft == snapshot and not core.stages and not core.cancels
            assert "answer_manual_once" not in app.user_data[ACTOR]

    asyncio.run(scenario())


def test_obsolete_other_draft_button_cannot_replace_current_active_draft():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            operation = await enter_amount(app, core, dispatch)
            fail_amount_operation(core, operation)
            await delivery_finished(app)
            current_id = uuid4()
            data = app.user_data[ACTOR]
            data.pop("answer_interpretation", None)
            data.pop("answer_manual_once", None)
            data[bot.WIZARD_DATA_KEY][bot.DRAFT_ID_KEY] = str(current_id)
            before = len(sent)

            await dispatch(bound_callback("manual", operation, DRAFT))

            assert data[bot.WIZARD_DATA_KEY][bot.DRAFT_ID_KEY] == str(current_id)
            assert "answer_manual_once" not in data
            assert not core.stages and not core.cancels
            assert any("другой черновик" in item.get("text", "") for item in sent[before:])

    asyncio.run(scenario())
