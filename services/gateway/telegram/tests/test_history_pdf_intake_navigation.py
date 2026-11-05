"""A historical PDF is read-only even with every intake extension installed."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import test_case_experience as runtime_harness
from telegram_gateway.answer_intent import bound_callback as answer_callback
from telegram_gateway.case_history_runtime import _pdf_callback
from telegram_gateway.understanding_intent import bound_callback as understanding_callback
from test_answer_runtime import answer_harness
from test_confirmed_understanding_runtime import TEXT, active_harness
from test_dialog_isolation import ACTOR, DRAFT


@pytest.mark.parametrize("phase", ["understanding", "answer"])
def test_history_pdf_preserves_pending_intake_and_ordinary_navigation_cancels(phase, monkeypatch):
    monkeypatch.delenv("AGENT_ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("AGENT_INTERNAL_KEY", raising=False)
    queue = AsyncMock()
    monkeypatch.setattr("telegram_gateway.case_history_runtime.queue_report_pdf", queue)
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
        async with harness() as (app, core, _sent, dispatch):
            if phase == "understanding":
                await dispatch("quick:start")
                await dispatch("text:" + TEXT)
                callback = understanding_callback
                selected_key = "understanding_intake"
            else:
                await dispatch(f"case:draft:{DRAFT}")
                await dispatch("text:Синтетический посетитель недоволен обслуживанием")
                callback = answer_callback
                selected_key = "answer_interpretation"
            operation = core.starts[-1]
            selected = deepcopy(app.user_data[ACTOR][selected_key])
            saved_draft = deepcopy(core.draft)
            # This button belongs to a previously displayed case, not the active draft.
            await dispatch(_pdf_callback(
                UUID("00000000-0000-0000-0000-000000000888"),
                UUID("00000000-0000-0000-0000-000000000999"), 0,
            ))
            queue.assert_awaited_once()
            assert core.cancels == []
            assert core.operations[operation]["status"]["state"] == "PENDING"
            assert core.draft == saved_draft
            assert app.user_data[ACTOR][selected_key] == selected
            # Its status controls still address the same in-flight operation afterward.
            await dispatch(callback("refresh", operation, DRAFT))
            assert core.cancels == []
            assert app.user_data[ACTOR][selected_key]["operationId"] == str(operation)
            await dispatch("menu")
            assert core.cancels == [operation]
            assert core.operations[operation]["status"]["state"] == "MANUAL_CONTINUATION"
            assert selected_key not in app.user_data[ACTOR]
    asyncio.run(scenario())
