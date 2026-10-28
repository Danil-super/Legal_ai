"""Unknown facts need a concrete question, not a frozen unrecoverable report."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx2
import pytest
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from telegram.ext import ApplicationHandlerStop
from telegram_gateway import bot
from telegram_gateway.analysis_jobs_runtime import JobSnapshot, job_message
from telegram_gateway.analysis_runtime import analysis_error_message
from telegram_gateway.case_wizard import LegalCoreApiError, LegalCoreClient
from telegram_gateway.clarification_intent import encode_clarification_callback
from telegram_gateway.clarification_runtime import create_clarification
from test_case_experience import harness
from test_dialog_isolation import ACTOR, DRAFT
from test_factual_safety_gateway import completed_data, runtime_context
from test_guided_case_intake_v2 import _base_data


@pytest.mark.parametrize("field", ["representativeContact", "authorityReferralMentioned"])
def test_explicit_unknown_safety_fact_is_preserved_at_summary_confirmation(field):
    screening = {"schemaVersion": SCREENING_VERSION,
                 **dict.fromkeys(SCREENING_FIELDS, "NO"), "amount": "NOT_REQUESTED"}
    screening[field] = "UNKNOWN"
    data = _base_data() | {"safetyScreening": screening}
    update, context, _client, message = runtime_context(data, "case:v2:summary:confirm")
    result = asyncio.run(bot.confirm_v2_summary(update, context))
    assert result == bot.WizardState.V2_CONFIRM
    assert data["safetyScreening"][field] == "UNKNOWN"
    assert all(data["safetyScreening"][f] == "NO" for f in SCREENING_FIELDS if f != field)
    assert any("подтверждена" in text.lower() for text in message.text_replies)


def test_stored_unknown_error_explains_the_actual_question():
    text = analysis_error_message("FACTUAL_SAFETY_REPRESENTATIVECONTACT_UNKNOWN")
    assert "представитель" in text.lower()
    assert "не удалось" not in text.lower()


def test_unknown_fact_job_offers_clarification_instead_of_repeating_same_analysis():
    case = UUID("00000000-0000-4000-8000-000000000099")
    job = JobSnapshot(UUID("00000000-0000-4000-8000-000000000098"), case, "FAILED",
                      "FACTUAL_SAFETY_REPRESENTATIVECONTACT_UNKNOWN", None)
    _text, keyboard = job_message(job)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert f"case:clarify:{case}" in callbacks
    assert f"case:analyze:{case}" not in callbacks


def test_clarification_retry_and_restart_use_same_intent_and_resume_the_new_draft():
    from telegram_gateway.clarification_runtime import create_clarification

    case = UUID("00000000-0000-4000-8000-000000000091")
    draft = UUID("00000000-0000-4000-8000-000000000092")
    client = SimpleNamespace(
        clarification_preview=AsyncMock(return_value={
            "caseId": str(case), "expectedFactSnapshotSha256": "a" * 64,
            "expectedReportId": str(REPORT), "expectedReportSha256": "b" * 64,
        }),
        create_clarification_draft=AsyncMock(return_value={
            "id": str(draft), "revision": 1, "wizardState": "SAFETY",
            "draftData": {"intakeVersion": 2, "correctionSource": {
                "caseId": str(case), "factSnapshotSha256": "a" * 64,
                "reportId": str(REPORT), "reportSha256": "b" * 64,
            }},
        }),
    )
    message = SimpleNamespace(message_id=77, reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=7000000001),
                             effective_message=message, callback_query=SimpleNamespace(
                                 data=encode_clarification_callback(case, REPORT),
                                 answer=AsyncMock(), message=message))
    for _ in range(2):
        context = SimpleNamespace(user_data={}, bot_data={bot.LEGAL_CORE_CLIENT_KEY: client})
        with pytest.raises(ApplicationHandlerStop):
            asyncio.run(create_clarification(update, context))
    first, second = client.create_clarification_draft.await_args_list
    assert first.kwargs["key"] == second.kwargs["key"]
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"case:draft:{draft}"
    assert markup.inline_keyboard[-1][0].callback_data == "menu"


def test_expired_clarification_explains_expiry_instead_of_endless_retry():
    from telegram_gateway.case_wizard import LegalCoreApiError
    from telegram_gateway.clarification_runtime import create_clarification

    case = UUID("00000000-0000-4000-8000-000000000091")
    client = SimpleNamespace(
        clarification_preview=AsyncMock(return_value={
            "caseId": str(case), "expectedFactSnapshotSha256": "a" * 64,
            "expectedReportId": str(REPORT), "expectedReportSha256": "b" * 64,
        }),
        create_clarification_draft=AsyncMock(side_effect=LegalCoreApiError(
            410, "INTAKE_DRAFT_EXPIRED", "SYNTHETIC_PRIVATE_BOUNDARY_DETAIL",
        )),
    )
    message = SimpleNamespace(message_id=77, reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=7000000001),
                             effective_message=message, callback_query=SimpleNamespace(
                                 data=encode_clarification_callback(case, REPORT),
                                 answer=AsyncMock(), message=message))
    context = SimpleNamespace(user_data={}, bot_data={bot.LEGAL_CORE_CLIENT_KEY: client})
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(create_clarification(update, context))
    text = message.reply_text.await_args.args[0]
    assert "срок хранения" in text.lower()
    assert "повторное нажатие" not in text.lower()
    assert "SYNTHETIC_PRIVATE_BOUNDARY_DETAIL" not in text
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[-1][0].callback_data == "menu"


CASE = UUID("00000000-0000-4000-8000-000000000091")
REPORT = UUID("00000000-0000-4000-8000-000000000093")
PREVIEW = {"caseId": str(CASE), "expectedFactSnapshotSha256": "a" * 64,
           "expectedReportId": str(REPORT), "expectedReportSha256": "b" * 64}
SOURCE = {"caseId": str(CASE), "factSnapshotSha256": "a" * 64,
          "reportId": str(REPORT), "reportSha256": "b" * 64}


def clarification_context(*, preview=None, source=None):
    data = completed_data() | {"correctionSource": deepcopy(SOURCE if source is None else source)}
    client = SimpleNamespace(
        clarification_preview=AsyncMock(return_value=deepcopy(
            PREVIEW if preview is None else preview)),
        create_clarification_draft=AsyncMock(return_value={
            "id": str(DRAFT), "revision": 1, "wizardState": "SAFETY", "draftData": data,
        }),
    )
    message = SimpleNamespace(message_id=77, reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=ACTOR), effective_message=message,
                             callback_query=SimpleNamespace(
                                 data=encode_clarification_callback(CASE, REPORT),
                                 answer=AsyncMock(), message=message))
    context = SimpleNamespace(user_data={}, bot_data={bot.LEGAL_CORE_CLIENT_KEY: client})
    return update, context, client, message


def open_clarification(update, context):
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(create_clarification(update, context))


def test_client_serializes_report_binding_and_preserves_historical_optional_fields():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(201, json={"id": str(DRAFT)})

    async def scenario():
        async with httpx2.AsyncClient(base_url="http://core.test",
                                     transport=httpx2.MockTransport(handle)) as http:
            client = LegalCoreClient(http)
            await client.create_clarification_draft(CASE, ACTOR, expected_sha256="a" * 64,
                expected_report_id=REPORT, expected_report_sha256="b" * 64, key=DRAFT)
            await client.create_clarification_draft(CASE, ACTOR,
                expected_sha256="a" * 64, key=DRAFT)

    asyncio.run(scenario())
    assert json.loads(requests[0].content) == {
        "expectedFactSnapshotSha256": "a" * 64,
        "expectedReportId": str(REPORT), "expectedReportSha256": "b" * 64,
    }
    assert json.loads(requests[1].content) == {"expectedFactSnapshotSha256": "a" * 64}
    assert requests[0].headers["X-Telegram-User-Id"] == str(ACTOR)
    assert requests[0].headers["Idempotency-Key"] == str(DRAFT)


@pytest.mark.parametrize("changes", [
    {"caseId": str(DRAFT)}, {"expectedFactSnapshotSha256": "x" * 64},
    {"expectedReportId": None}, {"expectedReportId": "not-a-uuid"},
    {"expectedReportSha256": None}, {"expectedReportSha256": "b" * 63},
    {"expectedReportSha256": "B" * 64}, {"expectedReportSha256": True},
])
def test_runtime_rejects_malformed_or_foreign_preview_before_creation(changes):
    update, context, client, message = clarification_context(preview=PREVIEW | changes)
    open_clarification(update, context)
    client.create_clarification_draft.assert_not_awaited()
    assert "Продолжить уточнение" not in str(message.reply_text.await_args)


@pytest.mark.parametrize("changes", [
    {"reportId": str(DRAFT)}, {"reportSha256": "c" * 64}, {"reportSha256": None},
    {"caseId": str(DRAFT)}, {"factSnapshotSha256": "d" * 64}, {"extra": "not-allowed"},
])
def test_runtime_rejects_returned_source_mismatch_before_continue(changes):
    update, context, _client, message = clarification_context(source=SOURCE | changes)
    open_clarification(update, context)
    assert "Продолжить уточнение" not in str(message.reply_text.await_args)
    assert bot.WIZARD_DATA_KEY not in context.user_data


def test_runtime_passes_report_binding_and_intent_changes_with_report_or_message():
    keys = []
    for report_id, report_hash, message_id in [
        (REPORT, "b" * 64, 77), (REPORT, "b" * 64, 77),
        (DRAFT, "b" * 64, 77), (REPORT, "c" * 64, 77), (REPORT, "b" * 64, 78),
    ]:
        update, context, client, message = clarification_context(
            preview=PREVIEW | {"expectedReportId": str(report_id),
                               "expectedReportSha256": report_hash},
            source=SOURCE | {"reportId": str(report_id), "reportSha256": report_hash},
        )
        message.message_id = message_id
        update.callback_query.data = encode_clarification_callback(CASE, report_id)
        open_clarification(update, context)
        args = client.create_clarification_draft.await_args
        assert args.args == (CASE, ACTOR)
        assert args.kwargs["expected_report_id"] == report_id
        assert args.kwargs["expected_report_sha256"] == report_hash
        keys.append(args.kwargs["key"])
    assert keys[0] == keys[1]
    assert len(set(keys)) == 4


@pytest.mark.parametrize("code", ["CASE_REPORT_STALE", "CLARIFICATION_REPORT_BINDING_REQUIRED",
                                  "CLARIFICATION_REPORT_INVALID"])
def test_report_errors_require_latest_report_without_exposing_private_details(code):
    update, context, client, message = clarification_context()
    client.create_clarification_draft.side_effect = LegalCoreApiError(
        409, code, "SYNTHETIC_PRIVATE_BOUNDARY_DETAIL")
    open_clarification(update, context)
    text = message.reply_text.await_args.args[0]
    assert "последн" in text.lower() and "отчёт" in text.lower()
    assert "SYNTHETIC_PRIVATE_BOUNDARY_DETAIL" not in text
    assert "повторное нажатие безопасно" not in text.lower()


def test_lost_gateway_reply_rechecks_core_and_reuses_same_creation_intent():
    update, context, client, message = clarification_context()
    message.reply_text.side_effect = RuntimeError("synthetic lost Telegram reply")
    with pytest.raises(RuntimeError, match="synthetic lost"):
        asyncio.run(create_clarification(update, context))
    message.reply_text.side_effect = None
    context.user_data = {}
    open_clarification(update, context)
    assert client.clarification_preview.await_count == 2
    first, second = client.create_clarification_draft.await_args_list
    assert first == second
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"case:draft:{DRAFT}"


def test_recovery_continues_through_production_composition_and_requires_new_confirmation():
    async def scenario():
        async with harness() as (app, core, sent, dispatch):
            old_data = completed_data()
            requests = [{"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0}]
            recovered = {"id": str(DRAFT), "revision": 1,
                         "wizardState": "ANALYSIS_CLARIFICATION",
                         "draftData": deepcopy(old_data) | {"correctionSource": SOURCE,
                             "analysisClarificationRequests": requests}}
            core.clarification_preview = AsyncMock(return_value=PREVIEW)
            core.create_clarification_draft = AsyncMock(return_value=deepcopy(recovered))
            core.draft = deepcopy(recovered)
            await dispatch(encode_clarification_callback(CASE, REPORT))
            text = sent[-1]["text"]
            assert "Старый кейс и отчёты не изменены" in text
            assert "Исходные файлы остаются в старом кейсе" in text
            assert "подтверждения" in text
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            await dispatch(f"case:draft:{DRAFT}")
            assert "Как оплачивалась услуга" in sent[-1]["text"]
            assert core.draft["draftData"]["eventSummary"] == old_data["eventSummary"]
            assert "analysisClarificationAnswers" not in core.draft["draftData"]
            await dispatch("case:v2:clarify:SERVICE_PAYMENT_BASIS:0:PAID")
            assert core.draft["wizardState"] == "SUMMARY"
            await dispatch("case:v2:summary:confirm")
            assert core.draft["wizardState"] == "V2_CONFIRM"
            assert old_data == completed_data()
    asyncio.run(scenario())


@pytest.mark.parametrize("report_id,report_hash", [
    (REPORT, None), (None, "b" * 64), (REPORT, "bad"), (REPORT, "B" * 64),
])
def test_client_rejects_partial_or_invalid_report_binding_without_http(report_id, report_hash):
    http = SimpleNamespace(request=AsyncMock())
    client = LegalCoreClient(http)
    with pytest.raises(ValueError):
        asyncio.run(client.create_clarification_draft(
            CASE, ACTOR, expected_sha256="a" * 64, key=DRAFT,
            expected_report_id=report_id, expected_report_sha256=report_hash,
        ))
    http.request.assert_not_awaited()


@pytest.mark.parametrize("message_id", [None, True, 0, -1])
def test_missing_message_identity_cannot_create_colliding_intent(message_id):
    update, context, client, message = clarification_context()
    message.message_id = message_id
    open_clarification(update, context)
    client.create_clarification_draft.assert_not_awaited()


@pytest.mark.parametrize("response", [None, [], {"id": str(DRAFT)},
                                     {"id": str(DRAFT), "revision": 0, "wizardState": "SAFETY",
                                      "draftData": {"intakeVersion": 2,
                                                    "correctionSource": SOURCE}}])
def test_invalid_draft_envelope_cannot_be_selected(response):
    update, context, client, message = clarification_context()
    client.create_clarification_draft.return_value = response
    open_clarification(update, context)
    assert "Продолжить уточнение" not in str(message.reply_text.await_args)


@pytest.mark.parametrize("boundary", ["clarification_preview", "create_clarification_draft"])
def test_revoked_creator_scope_never_restores_cached_draft(boundary):
    update, context, client, message = clarification_context()
    getattr(client, boundary).side_effect = LegalCoreApiError(
        404, "CASE_NOT_FOUND", "SYNTHETIC_PRIVATE_BOUNDARY_DETAIL")
    open_clarification(update, context)
    assert "Продолжить уточнение" not in str(message.reply_text.await_args)
    assert "SYNTHETIC_PRIVATE_BOUNDARY_DETAIL" not in str(message.reply_text.await_args)
    assert bot.WIZARD_DATA_KEY not in context.user_data
