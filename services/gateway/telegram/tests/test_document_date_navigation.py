"""Date selection traverses the full production handler chain before upload."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest
import test_case_experience as runtime_harness
from test_dialog_isolation import ACTOR


@pytest.fixture
def document_harness(monkeypatch):
    monkeypatch.delenv("AGENT_ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("AGENT_INTERNAL_KEY", raising=False)
    factory = runtime_harness.build_application_with_case_experience
    monkeypatch.setattr(
        runtime_harness, "build_application_with_case_experience",
        lambda token: factory(token, enable_understanding=True,
                              enable_answer_interpretation=True, enable_simplified_dialogue=True),
    )
    client = AsyncMock()
    client.get_actor.return_value = {"role": "CLINIC_OWNER"}
    monkeypatch.setattr(
        "telegram_gateway.clinic_document_runtime.ClinicDocumentCoreClient", lambda: client,
    )
    return runtime_harness.harness


@pytest.mark.parametrize("date_action", ["today", "manual", "text"])
def test_document_date_preserves_category_and_advances_to_file_upload(
    document_harness, date_action,
):
    async def scenario():
        async with document_harness() as (app, _core, sent, dispatch):
            await dispatch("cliniclib:category:contract")
            selected = dict(app.user_data[ACTOR]["clinic_document_effective_date_pending"])
            assert selected["document_type"] == "CONTRACT"
            assert selected["document_key"] == "service-contract"
            if date_action != "text":
                callback = "cliniclib:date:" + date_action
                assert any(button.callback_data == callback
                           for row in sent[-1]["reply_markup"].inline_keyboard for button in row)
                await dispatch(callback)
            if date_action != "today":
                assert app.user_data[ACTOR]["clinic_document_effective_date_pending"] == selected
                await dispatch("text:2026-01-15")
            assert "clinic_document_effective_date_pending" not in app.user_data[ACTOR]
            assert app.user_data[ACTOR]["clinic_document_upload"] == {
                **selected,
                "valid_from": date.today().isoformat() if date_action == "today" else "2026-01-15",
            }
            assert "Режим загрузки документа включён" in sent[-1]["text"]
            # Ordinary navigation still leaves the file-input mode.
            await dispatch("menu")
            assert "clinic_document_upload" not in app.user_data[ACTOR]
    asyncio.run(scenario())


@pytest.mark.parametrize("navigation", ["cliniclib:date:cancel", "cliniclib:upload", "menu"])
def test_leaving_document_date_selection_clears_pending_input(document_harness, navigation):
    async def scenario():
        async with document_harness() as (app, _core, _sent, dispatch):
            await dispatch("cliniclib:category:contract")
            assert "clinic_document_effective_date_pending" in app.user_data[ACTOR]
            await dispatch(navigation)
            assert "clinic_document_effective_date_pending" not in app.user_data[ACTOR]
            assert "clinic_document_upload" not in app.user_data[ACTOR]
            await dispatch("text:2026-01-15")
            assert "clinic_document_upload" not in app.user_data[ACTOR]
    asyncio.run(scenario())
