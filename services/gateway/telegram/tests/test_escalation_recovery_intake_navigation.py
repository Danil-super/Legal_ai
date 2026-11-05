"""Old operation recovery preserves a new intake through every PTB handler group."""

import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
import test_case_experience as runtime_harness
from telegram import Update
from telegram_gateway.answer_intent import bound_callback as answer_callback
from telegram_gateway.understanding_intent import bound_callback as understanding_callback
from test_answer_runtime import answer_harness
from test_confirmed_understanding_runtime import TEXT, active_harness
from test_dialog_isolation import ACTOR, DRAFT


@pytest.mark.parametrize("phase", ["understanding", "answer"])
@pytest.mark.parametrize("mode", ["m", "c"])
@pytest.mark.parametrize("navigation", ["menu", "ordinary-escalation"])
def test_old_recovery_preserves_pending_intake_but_ordinary_navigation_cancels(
    phase, mode, navigation, monkeypatch,
):
    monkeypatch.delenv("AGENT_ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("AGENT_INTERNAL_KEY", raising=False)
    factory = runtime_harness.build_application_with_case_experience
    monkeypatch.setattr(
        runtime_harness, "build_application_with_case_experience",
        lambda token, **_flags: factory(
            token, enable_understanding=True, enable_answer_interpretation=True,
            enable_simplified_dialogue=True,
        ),
    )

    async def scenario():
        harness = active_harness if phase == "understanding" else answer_harness
        async with harness() as (app, core, sent, dispatch):
            if phase == "understanding":
                await dispatch("quick:start")
                await dispatch("text:" + TEXT)
                callback, selected_key = understanding_callback, "understanding_intake"
            else:
                await dispatch(f"case:draft:{DRAFT}")
                await dispatch("text:Synthetic visitor describes a service problem.")
                callback, selected_key = answer_callback, "answer_interpretation"
            operation = core.starts[-1]
            before_data, before_draft = deepcopy(app.user_data[ACTOR]), deepcopy(core.draft)
            before_operation = deepcopy(core.operations[operation])
            escalation = uuid4()
            core.assigned, core.status = True, "IN_PROGRESS"
            user = {"id": ACTOR, "is_bot": False, "first_name": "Synthetic"}
            chat = {"id": ACTOR, "type": "private"}
            original = {"message_id": 321, "date": 1, "from": user, "chat": chat,
                        "text": "Synthetic recovery note."}
            message = {"message_id": 322, "date": 1, "chat": chat,
                       "from": {"id": 123456, "is_bot": True, "first_name": "SyntheticBot"},
                       "reply_to_message": original}
            event = {"update_id": 999, "callback_query": {
                "id": "recovery-regression", "chat_instance": "synthetic", "from": user,
                "message": message, "data": f"esc:retrymsg:{escalation.hex}:{mode}:321",
            }}
            await app.process_update(Update.de_json(event, app.bot))
            assert core.cancels == []
            assert core.operations[operation] == before_operation
            assert core.operations[operation]["status"]["state"] == "PENDING"
            assert core.draft == before_draft
            assert app.user_data[ACTOR] == before_data
            assert len(core.posts if mode == "m" else core.conclusions) == 1
            assert "escalation_discussion_id" not in app.user_data[ACTOR]
            assert any("Отправка проверена" in item.get("text", "") for item in sent)
            await dispatch(callback("refresh", operation, DRAFT))
            assert core.cancels == []
            assert app.user_data[ACTOR][selected_key]["operationId"] == str(operation)
            # A blanket exemption for esc:* would incorrectly pass the recovery half.
            # Ordinary escalation navigation must still cancel the current model work.
            await dispatch("menu" if navigation == "menu" else f"esc:claim:{escalation}")
            assert core.cancels == [operation]
            assert core.operations[operation]["status"]["state"] == "MANUAL_CONTINUATION"
            assert selected_key not in app.user_data[ACTOR]

    asyncio.run(scenario())
