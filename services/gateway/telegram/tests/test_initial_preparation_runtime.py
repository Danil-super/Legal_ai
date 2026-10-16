"""Explicit preparation confirmation and recovery are not report delivery."""

import asyncio
import os
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx2
import pytest
from sqlalchemy import text
from telegram.error import TelegramError
from telegram.ext import ConversationHandler
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreClient
from test_case_api import actor_headers
from test_case_experience import harness
from test_case_wizard import FakeQuery
from test_factual_safety_gateway import completed_data, runtime_context
from test_preparation_workflow_readers import _wire
from test_report_free_workflow import isolated_database as isolated_database
from test_report_free_workflow import preparation_workflow as preparation_workflow


def _context(*, event=None, unknown=False):
    data = completed_data(**({"representativeContact": "UNKNOWN"} if unknown else {}))
    if event is not None:
        data["eventDate"] = event
    update, context, client, message = runtime_context(data, "synthetic")
    workflow = UUID(data["workflow_id"])
    update.callback_query = FakeQuery(f"case:confirm2:{workflow}")
    client.submit_workflow = AsyncMock(return_value=_wire(workflow, 2))
    client.archive_intake_draft = AsyncMock()
    client.download_pdf = AsyncMock(side_effect=AssertionError("no preliminary PDF"))
    return update, context, client, message, workflow


def _callbacks(message):
    return [button.callback_data for markup in message.text_reply_markups if markup is not None
            for row in markup.inline_keyboard for button in row]


@pytest.mark.parametrize("urgent", [False, True])
def test_preparation_receipt_has_one_next_step_and_never_downloads_an_intake_pdf(
    monkeypatch, urgent,
):
    from telegram_gateway import analysis_runtime

    monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://synthetic-agent:8010")
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "synthetic-" + "x" * 40)
    monkeypatch.setattr(bot, "_send_workflow_report", bot._send_workflow_report)
    monkeypatch.setattr(analysis_runtime, "_PATCHED", False)
    analysis_runtime.build_application_with_analysis("123456789:synthetic-telegram-token")
    update, _, client, message, workflow = _context()
    wire = _wire(workflow, 2)
    escalation = uuid4() if urgent else None
    wire["case"]["earlyEscalationId"] = str(escalation) if escalation else None
    asyncio.run(bot._send_workflow_report(update, client, wire, 7000000001))
    assert message.documents == []
    client.download_pdf.assert_not_awaited()
    callbacks = _callbacks(message)
    if urgent:
        assert f"case:escalation:{escalation}" in callbacks
        assert not any(value.startswith("case:analyze:") for value in callbacks)
    else:
        assert callbacks.count(f"case:analyze:{wire['case']['id']}") == 1
    assert not any("pdf" in value or value.startswith("case:confirm") for value in callbacks)
    assert "отчёт готов" not in " ".join(message.text_replies).lower()
    assert "Hermes" not in " ".join(message.text_replies)


@pytest.mark.parametrize("change", [
    {"receiptVersion": 2.0}, {"receiptVersion": True}, {"receiptVersion": 3},
    {"report": None}, {"state": "RUNNING"},
])
def test_invalid_new_receipt_is_not_presented_as_success(change):
    update, _, client, message, workflow = _context()
    with pytest.raises(ValueError):
        asyncio.run(bot._send_workflow_report(update, client, _wire(workflow, 2) | change,
                                              7000000001))
    assert message.text_replies == [] and message.documents == []
    client.download_pdf.assert_not_awaited()


@pytest.mark.parametrize("event", [
    {"date": "2026-09-12", "precision": "EXACT"},
    {"date": None, "precision": "UNKNOWN"},
    {"date": "2026-09-01", "precision": "APPROXIMATE"},
])
def test_explicit_new_confirmation_keeps_attempts_without_an_automatic_repeat(event):
    update, context, client, message, _ = _context(event=event, unknown=True)
    before = deepcopy(context.user_data[bot.WIZARD_DATA_KEY])
    result = asyncio.run(bot.confirm_case(update, context))
    assert result == ConversationHandler.END
    client.submit_workflow.assert_awaited_once()
    call = client.submit_workflow.await_args
    assert call.kwargs == {"intake_schema_version": "dental-case-intake.v2", "receipt_version": 2}
    facts = {fact["factKey"]: fact["value"] for fact in call.args[1]}
    assert facts["EVENT_DATE"] == event
    assert facts["FACTUAL_SAFETY_SCREENING"]["representativeContact"] == "UNKNOWN"
    assert before["eventDate"] == event
    assert bot.WIZARD_DATA_KEY not in context.user_data
    client.download_pdf.assert_not_awaited()
    assert message.documents == []


def test_new_confirmation_still_requires_missing_fields_and_matching_workflow():
    update, context, client, _, workflow = _context()
    update.callback_query.data = f"case:confirm2:{uuid4()}"
    asyncio.run(bot.confirm_case(update, context))
    client.submit_workflow.assert_not_awaited()
    update.callback_query.data = f"case:confirm2:{workflow}"
    context.user_data[bot.WIZARD_DATA_KEY].pop("eventSummary")
    assert asyncio.run(bot.confirm_case(update, context)) == bot.WizardState.EVENT
    client.submit_workflow.assert_not_awaited()


def test_new_confirmation_rejects_a_legacy_result_without_delivering_its_pdf():
    update, context, client, message, workflow = _context()
    client.submit_workflow.return_value = _wire(workflow, 1)
    result = asyncio.run(bot.confirm_case(update, context))
    client.submit_workflow.assert_awaited_once()
    assert result == bot.WizardState.V2_CONFIRM
    client.download_pdf.assert_not_awaited()
    client.archive_intake_draft.assert_not_awaited()
    assert message.documents == []


def test_restart_recovers_the_stored_preparation_without_resubmitting_or_downloading():
    update, context, client, message, workflow = _context()
    context.user_data = {}
    client.get_workflow = AsyncMock(return_value=_wire(workflow, 2))
    asyncio.run(bot.resume_workflow(update, context))
    client.get_workflow.assert_awaited_once_with(workflow, 7000000001)
    client.submit_workflow.assert_not_awaited()
    client.download_pdf.assert_not_awaited()
    assert any(value.startswith("case:analyze:") for value in _callbacks(message))


def test_preparation_confirmation_control_preserves_default_legacy_namespace():
    workflow = uuid4()
    old = bot.confirm_keyboard(workflow).inline_keyboard[0][0]
    new = bot.confirm_keyboard(workflow, preparation=True).inline_keyboard[0][0]
    assert old.callback_data == f"case:confirm:{workflow}"
    assert new.callback_data == f"case:confirm2:{workflow}"
    assert len(new.callback_data.encode()) <= 64
    assert new.text == "✅ Подготовить рекомендации"


def test_lost_telegram_reply_preserves_the_confirmation_intent_for_explicit_retry():
    update, context, client, message, _ = _context()
    original = message.reply_text
    message.reply_text = AsyncMock(side_effect=[TelegramError("synthetic loss"), SimpleNamespace()])
    assert asyncio.run(bot.confirm_case(update, context)) == bot.WizardState.V2_CONFIRM
    client.submit_workflow.assert_awaited_once()
    first = deepcopy(client.submit_workflow.await_args.args)
    message.reply_text = original
    assert asyncio.run(bot.confirm_case(update, context)) == ConversationHandler.END
    assert client.submit_workflow.await_args.args == first
    assert client.submit_workflow.await_count == 2
    assert message.documents == []


@pytest.mark.parametrize("event", [
    {"date": "2026-02-30", "precision": "APPROXIMATE"},
    {"date": None, "precision": "APPROXIMATE"},
    {"date": "2026-09-12", "precision": "UNKNOWN"},
    {"date": "2026-09-12", "precision": "invented"},
])
def test_preparation_date_attempt_is_still_a_validated_calendar_value(event):
    update, context, client, _, _ = _context(event=event)
    assert asyncio.run(bot.confirm_case(update, context)) == bot.WizardState.EVENT_DATE
    client.submit_workflow.assert_not_awaited()


def test_approximate_event_date_is_visible_as_approximate_in_the_confirmation_summary():
    from telegram_gateway.guided_case_intake_v2 import review_blocks

    data = completed_data()
    data["eventDate"] = {"date": "2026-09-01", "precision": "APPROXIMATE"}
    assert "Когда: примерно 2026-09-01" in review_blocks(data)


@pytest.mark.parametrize("mode", ["confirm", "confirm2"])
@pytest.mark.parametrize("precision", ["EXACT", "APPROXIMATE"])
def test_future_event_date_remains_invalid_in_both_confirmation_modes(mode, precision):
    update, context, client, _, workflow = _context(
        event={"date": "9999-12-31", "precision": precision},
    )
    update.callback_query.data = f"case:{mode}:{workflow}"
    assert asyncio.run(bot.confirm_case(update, context)) == bot.WizardState.EVENT_DATE
    client.submit_workflow.assert_not_awaited()


def test_production_application_routes_restart_confirmation_to_read_only_recovery():
    async def run():
        async with harness() as (_, core, sent, dispatch):
            workflow = uuid4()
            core.get_workflow = AsyncMock(return_value=_wire(workflow, 2))
            await dispatch(f"case:confirm2:{workflow}")
            core.get_workflow.assert_awaited_once()
            assert any("карточка исходных сведений" in item.get("text", "") for item in sent)
            assert not any("document" in item for item in sent)

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="isolated PostgreSQL")
@pytest.mark.parametrize("precision", ["UNKNOWN", "APPROXIMATE"])
@pytest.mark.parametrize("loss", [False, True])
def test_real_gateway_confirmation_preserves_mode_attempts_and_one_case_after_http_loss(
    preparation_workflow, precision, loss,
):
    core, actor, _, _, engine = preparation_workflow
    event = {"date": None if precision == "UNKNOWN" else "2026-09-01", "precision": precision}
    update, context, _, message, _ = _context(event=event, unknown=True)
    update.effective_user.id = actor
    created = core.post("/v1/telegram-intake-drafts", headers=actor_headers(actor, uuid4()),
                        json={"intakeSchemaVersion": "dental-case-intake.v2"})
    assert created.status_code == 201, created.text
    draft = created.json()["id"]
    data = context.user_data[bot.WIZARD_DATA_KEY]
    saved = core.put(f"/v1/telegram-intake-drafts/{draft}",
                     headers=actor_headers(actor, uuid4()), json={
                         "expectedRevision": 1, "wizardState": "V2_CONFIRM",
                         "draftData": bot._draft_payload(data),
                     })
    assert saved.status_code == 200, saved.text
    data.update(workflow_id=draft, draft_id=draft, draft_revision=saved.json()["revision"],
                draft_state="V2_CONFIRM")
    update.callback_query = FakeQuery(f"case:confirm2:{draft}")
    submissions = []

    def bridge(request):
        actual = core.request(request.method, str(request.url), headers=dict(request.headers),
                              content=request.content)
        if request.url.path.endswith("/submissions"):
            submissions.append((request.content, actual.status_code))
            if loss and len(submissions) == 1:
                raise httpx2.ReadTimeout("synthetic lost submission reply", request=request)
        assert not request.url.path.endswith("/pdf")
        return httpx2.Response(actual.status_code, content=actual.content,
                               headers={"Content-Type": "application/json"})

    async def run():
        async with httpx2.AsyncClient(base_url="http://core.test",
                                     transport=httpx2.MockTransport(bridge)) as http:
            context.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = LegalCoreClient(http)
            if loss:
                assert await bot.confirm_case(update, context) == bot.WizardState.V2_CONFIRM
            assert await bot.confirm_case(update, context) == ConversationHandler.END
            context.user_data = {}
            await bot.resume_workflow(update, context)

    asyncio.run(run())
    assert [status for _, status in submissions] == ([201, 200] if loss else [201])
    assert len({body for body, _ in submissions}) == 1
    assert message.documents == []
    assert any(value.startswith("case:analyze:") for value in _callbacks(message))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM cases")) == 1
        assert connection.scalar(text("SELECT count(*) FROM case_reports")) == 0
        assert connection.scalar(text("SELECT receipt_version FROM telegram_case_workflows")) == 2
        assert connection.scalar(text(
            "SELECT value_json FROM case_facts WHERE fact_key='EVENT_DATE'"
        )) == event
