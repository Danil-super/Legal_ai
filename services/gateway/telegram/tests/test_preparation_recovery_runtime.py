"""Intermediate clarification buttons recover one owned draft, never a fake report."""

import asyncio
import os
from copy import deepcopy
from uuid import UUID, uuid4

import httpx2
import pytest
from sqlalchemy import text
from telegram.ext import ApplicationHandlerStop
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError, LegalCoreClient
from telegram_gateway.clarification_intent import encode_preparation_callback
from test_case_api import seed_admin
from test_clarification_recovery import _source_rows
from test_factual_clarification import (
    ACTOR,
    CASE,
    DRAFT,
    clarification_context,
    open_clarification,
)
from test_preparation_analysis import _submission
from test_preparation_analysis import prepared_case as prepared_case
from test_preparation_precontext import frozen_precontext as frozen_precontext
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

OPERATION = UUID("00000000-0000-4000-8000-000000000094")
PREVIEW = {"caseId": str(CASE), "expectedFactSnapshotSha256": "a" * 64,
           "expectedOperationId": str(OPERATION), "expectedOperationSha256": "b" * 64}
SOURCE = {"caseId": str(CASE), "factSnapshotSha256": "a" * 64,
          "operationId": str(OPERATION), "operationSha256": "b" * 64}


def _context():
    update, context, client, message = clarification_context(preview=PREVIEW, source=SOURCE)
    update.callback_query.data = encode_preparation_callback(CASE, OPERATION)
    return update, context, client, message


def test_intermediate_button_binds_full_origin_and_survives_gateway_restart():
    update, context, client, message = _context()
    open_clarification(update, context)
    client.clarification_preview.assert_awaited_once_with(
        CASE, ACTOR, source_operation_id=OPERATION,
    )
    first = client.create_clarification_draft.await_args
    assert first.args == (CASE, ACTOR)
    assert first.kwargs == {"expected_sha256": "a" * 64, "key": first.kwargs["key"],
                            "expected_operation_id": OPERATION,
                            "expected_operation_sha256": "b" * 64}
    context.user_data = {}
    open_clarification(update, context)
    assert client.create_clarification_draft.await_args == first
    buttons = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert buttons[0][0].callback_data == f"case:draft:{DRAFT}"
    text = message.reply_text.await_args.args[0]
    assert "Hermes" not in text and "PDF" not in text


@pytest.mark.parametrize("field,value", [
    ("caseId", str(uuid4())), ("expectedOperationId", str(uuid4())),
    ("expectedOperationSha256", "b" * 63), ("expectedFactSnapshotSha256", None),
    ("expectedReportId", str(uuid4())),
])
def test_wrong_or_ambiguous_preview_cannot_create_a_draft(field, value):
    update, context, client, _ = _context()
    client.clarification_preview.return_value[field] = value
    open_clarification(update, context)
    client.clarification_preview.assert_awaited_once()
    client.create_clarification_draft.assert_not_awaited()


@pytest.mark.parametrize("field", ["caseId", "operationId", "operationSha256",
                                   "factSnapshotSha256"])
def test_wrong_returned_draft_origin_never_offers_a_resume_button(field):
    update, context, client, message = _context()
    source = client.create_clarification_draft.return_value["draftData"]["correctionSource"]
    source[field] = str(uuid4()) if field.endswith("Id") else "c" * 64
    open_clarification(update, context)
    client.create_clarification_draft.assert_awaited_once()
    buttons = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(button.callback_data.startswith("case:draft:")
                   for row in buttons for button in row)


@pytest.mark.parametrize("field,value", [
    ("unexpectedProperty", "synthetic"),
    ("safetyScreening", {"schemaVersion": "invented", "hospitalizationReported": "SAFE"}),
    ("analysisClarificationRequests", [{"questionId": "INVENTED"}]),
    ("analysisClarificationAnswers", {"schemaVersion": "invented"}),
    ("eventSummary", "x" * 20_000),
    ("eventSummary", "я" * 9_000),
    ("incident_type", "OTHER"),
], ids=["unexpected", "safety", "questions", "answers", "oversize", "utf8-size", "mixed-v1"])
def test_returned_draft_content_must_pass_the_same_bounded_schema_as_core(field, value):
    from legal_core.api_contracts import TelegramIntakeDraftUpdateRequest

    update, context, client, message = _context()
    draft = client.create_clarification_draft.return_value
    draft["draftData"][field] = value
    with pytest.raises(ValueError):
        TelegramIntakeDraftUpdateRequest.model_validate({
            "expectedRevision": draft["revision"], "wizardState": draft["wizardState"],
            "draftData": draft["draftData"],
        })
    open_clarification(update, context)
    client.create_clarification_draft.assert_awaited_once()
    buttons = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert not any(button.callback_data.startswith("case:draft:")
                   for row in buttons for button in row)
    assert context.user_data == {}


def test_core_and_gateway_share_the_partial_draft_validator_without_duplicate_origin_types():
    from legal_core import api_contracts, contracts
    from telegram_gateway import clarification_runtime

    assert api_contracts.CorrectionSource is contracts.CorrectionSource
    assert api_contracts.validate_intake_draft_data is contracts.validate_intake_draft_data
    assert clarification_runtime.validate_intake_draft_data is contracts.validate_intake_draft_data


@pytest.mark.parametrize("code", ["CASE_PREPARATION_STALE", "CLARIFICATION_OPERATION_UNAVAILABLE",
                                  "PREPARATION_RESULT_INVALID", "CASE_CONTENT_EXPIRED"])
def test_stale_invalid_or_expired_source_does_not_fall_back_to_a_report(code):
    update, context, client, message = _context()
    client.clarification_preview.side_effect = LegalCoreApiError(409, code, "PRIVATE_TEST_DETAIL")
    open_clarification(update, context)
    assert client.clarification_preview.await_count == 1
    client.create_clarification_draft.assert_not_awaited()
    text = message.reply_text.await_args.args[0]
    assert "PRIVATE_TEST_DETAIL" not in text
    assert "Повторное нажатие безопасно" not in text


@pytest.mark.parametrize("message_id", [None, True, 0, -1])
def test_invalid_message_identity_cannot_start_a_creation_intent(message_id):
    update, context, client, _ = _context()
    update.callback_query.message.message_id = message_id
    open_clarification(update, context)
    client.create_clarification_draft.assert_not_awaited()


def test_lost_telegram_reply_reuses_the_same_intent_after_restart():
    from telegram_gateway.clarification_runtime import create_clarification

    update, context, client, message = _context()
    message.reply_text.side_effect = RuntimeError("synthetic lost Telegram reply")
    with pytest.raises(RuntimeError, match="lost Telegram reply"):
        asyncio.run(create_clarification(update, context))
    client.create_clarification_draft.assert_awaited_once()
    first = (client.create_clarification_draft.await_args.args,
             deepcopy(client.create_clarification_draft.await_args.kwargs))
    message.reply_text.side_effect = None
    context.user_data = {}
    open_clarification(update, context)
    repeated = client.create_clarification_draft.await_args
    assert (repeated.args, repeated.kwargs) == first


def test_actual_callback_router_accepts_the_new_namespace():
    from telegram import Update

    app = bot.build_application("000000000:synthetic-test-token")
    update = Update.de_json({"update_id": 1, "callback_query": {
        "id": "synthetic", "from": {"id": ACTOR, "is_bot": False, "first_name": "Test"},
        "chat_instance": "synthetic", "data": encode_preparation_callback(CASE, OPERATION),
    }}, app.bot)
    handlers = app.handlers[-2]
    assert any(handler.check_update(update) for handler in handlers)


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="isolated PostgreSQL")
@pytest.mark.parametrize("questions", [True, False])
@pytest.mark.parametrize("loss", ["none", "http", "telegram"])
def test_real_gateway_core_recovery_and_restart_create_one_bound_draft(
    prepared_case, questions, loss,
):
    from telegram_gateway.clarification_runtime import create_clarification

    core, actor, _, case, headers, context_data, engine = prepared_case
    response = core.post(f"/v1/cases/{case}/preparation-submissions", headers=headers,
                         json=_submission(context_data, questions=questions))
    assert response.status_code == 201, response.text
    result = response.json()
    before = _source_rows(case)
    update, context, _, message = _context()
    update.effective_user.id = actor
    update.callback_query.data = encode_preparation_callback(
        UUID(case), UUID(result["source"]["operationId"]),
    )
    posts, statuses, lost = [], [], [False]

    def bridge(request):
        actual = core.request(request.method, str(request.url), headers=dict(request.headers),
                              content=request.content)
        if request.method == "POST":
            posts.append(request.headers["Idempotency-Key"])
            statuses.append(actual.status_code)
            if loss == "http" and not lost[0]:
                lost[0] = True
                raise httpx2.ReadTimeout("synthetic lost Core response", request=request)
        return httpx2.Response(actual.status_code, content=actual.content,
                               headers={"Content-Type": "application/json"})

    async def run():
        async with httpx2.AsyncClient(base_url="http://core.test",
                                     transport=httpx2.MockTransport(bridge)) as http:
            context.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = LegalCoreClient(http)
            if loss == "telegram":
                message.reply_text.side_effect = RuntimeError("synthetic lost Telegram reply")
                with pytest.raises(RuntimeError, match="lost Telegram reply"):
                    await create_clarification(update, context)
                message.reply_text.side_effect = None
            else:
                with pytest.raises(ApplicationHandlerStop):
                    await create_clarification(update, context)
            context.user_data = {}
            with pytest.raises(ApplicationHandlerStop):
                await create_clarification(update, context)
            assert statuses == [201, 200]
            assert posts[0] == posts[1]
            controls = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
            selected = controls[0][0].callback_data
            assert selected.startswith("case:draft:")
            with engine.connect() as connection:
                drafts = connection.execute(text(
                    "SELECT id,draft_json FROM telegram_intake_drafts"
                ))
                draft = drafts.one()
                assert selected == f"case:draft:{draft.id}"
                assert draft.draft_json["correctionSource"]["operationId"] == (
                    result["source"]["operationId"]
                )
            # A forged button from another tenant cannot turn the owner intent into a new draft.
            foreign = actor + 1_000_000_000
            seed_admin(foreign)
            update.effective_user.id = foreign
            context.user_data = {}
            with pytest.raises(ApplicationHandlerStop):
                await create_clarification(update, context)
            assert len(posts) == 2
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM telegram_intake_drafts")) == 1

    asyncio.run(run())
    assert _source_rows(case) == before
    assert before["case_reports"] == []


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="isolated PostgreSQL")
@pytest.mark.parametrize("frozen_precontext", [{
    "eventDate": {"date": None, "precision": "UNKNOWN"},
}], indirect=True)
def test_real_precontext_button_preserves_unknown_date_without_a_fake_report(frozen_precontext):
    from telegram_gateway.clarification_runtime import create_clarification

    core, actor, case, headers, engine = frozen_precontext
    source = core.post(f"/v1/cases/{case}/preparation-context", headers=headers)
    assert source.status_code == 201, source.text
    result = source.json()
    assert result["source"]["context"] is None
    update, context, _, message = _context()
    update.effective_user.id = actor
    update.callback_query.data = encode_preparation_callback(
        UUID(case), UUID(result["source"]["operationId"]),
    )

    def bridge(request):
        response = core.request(request.method, str(request.url), headers=dict(request.headers),
                                content=request.content)
        return httpx2.Response(response.status_code, content=response.content,
                               headers={"Content-Type": "application/json"})

    async def run():
        async with httpx2.AsyncClient(base_url="http://core.test",
                                     transport=httpx2.MockTransport(bridge)) as http:
            context.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = LegalCoreClient(http)
            with pytest.raises(ApplicationHandlerStop):
                await create_clarification(update, context)

    asyncio.run(run())
    with engine.connect() as connection:
        state, data = connection.execute(text(
            "SELECT wizard_state,draft_json FROM telegram_intake_drafts"
        )).one()
        assert state == "V2_CONFIRM"
        assert data["eventDate"] == {"date": None, "precision": "UNKNOWN"}
        assert connection.scalar(text("SELECT count(*) FROM case_reports WHERE case_id=:case"),
                                 {"case": case}) == 0
    controls = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert controls[0][0].callback_data.startswith("case:draft:")
