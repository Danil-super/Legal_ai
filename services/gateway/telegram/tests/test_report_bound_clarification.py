"""Synthetic regression for one immutable displayed-report recovery intent."""

import asyncio
import os
from base64 import urlsafe_b64encode
from copy import deepcopy
from uuid import UUID, uuid4

import httpx2
import pytest
import test_analysis_clarification_recovery as recovery_tests
from legal_core.database import owner_database_url
from sqlalchemy import create_engine, text
from telegram_gateway.analysis_jobs_runtime import JobSnapshot, job_message
from telegram_gateway.case_wizard import LegalCoreClient
from telegram_gateway.clarification_runtime import create_clarification
from test_case_api import actor_headers, application_client, seed_admin
from test_clarification_recovery import _confirm, _guided_data, _source_rows
from test_factual_clarification import PREVIEW, clarification_context, open_clarification

isolated_database = recovery_tests.isolated_database
protected_database = recovery_tests.protected_database
_append_report = recovery_tests._append_report


def bound_callback(case_id, report_id):
    return "case:clarify:" + urlsafe_b64encode(
        UUID(str(case_id)).bytes + UUID(str(report_id)).bytes
    ).decode("ascii").rstrip("=")


def test_compact_callback_has_canonical_uuid_pair_and_fits_telegram_limit():
    from telegram_gateway.clarification_intent import (
        decode_clarification_callback,
        encode_clarification_callback,
    )
    case, report = uuid4(), uuid4()
    value = encode_clarification_callback(case, report)
    assert value == bound_callback(case, report)
    assert len(value.encode()) == 56
    assert decode_clarification_callback(value) == (case, report)


@pytest.mark.parametrize("token", ["", "A" * 42, "A" * 44, "A" * 42 + "=",
                                   "A" * 42 + "+", "A" * 42 + "B", "ф" * 43])
def test_malformed_noncanonical_callback_cannot_decode(token):
    from telegram_gateway.clarification_intent import decode_clarification_callback
    with pytest.raises(ValueError):
        decode_clarification_callback("case:clarify:" + token)


def test_legacy_case_only_button_explicitly_requires_latest_report_without_creating():
    update, context, client, message = clarification_context()
    update.callback_query.data = f"case:clarify:{PREVIEW['caseId']}"
    open_clarification(update, context)
    client.create_clarification_draft.assert_not_awaited()
    text = message.reply_text.await_args.args[0]
    assert "устарела" in text.lower() and "последн" in text.lower()
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == bound_callback(
        PREVIEW["caseId"], PREVIEW["expectedReportId"])


def test_client_serializes_explicit_source_report_selector():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx2.Response(200, json=PREVIEW)
    async def scenario():
        async with httpx2.AsyncClient(base_url="http://core.test",
                                     transport=httpx2.MockTransport(handle)) as http:
            await LegalCoreClient(http).clarification_preview(
                UUID(PREVIEW["caseId"]), 7000000001,
                source_report_id=UUID(PREVIEW["expectedReportId"]),
            )
    asyncio.run(scenario())
    assert requests[0].url.params["report_id"] == PREVIEW["expectedReportId"]


def test_success_result_controls_bind_the_actual_displayed_report_and_case():
    from test_telegram_report_presentation import _clarification_report
    case, report = uuid4(), uuid4()
    canonical = _clarification_report()
    canonical["reportId"] = str(report)
    canonical["case"]["id"] = str(case)
    result = {"analysisAllowed": False, "escalationRequired": False,
              "report": {"id": str(report), "caseId": str(case), "reportJson": canonical}}
    _text, keyboard = job_message(JobSnapshot(uuid4(), case, "SUCCEEDED", None, result))
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert bound_callback(case, report) in callbacks
    assert f"case:clarify:{case}" not in callbacks


@pytest.mark.parametrize("invalid", [
    {"summary": None}, {"summary": {"analysisAvailability": None}}, {"facts": None},
    {"clarificationRequests": [{"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 4}]},
    {"clarificationRequests": [{"questionId": "RESPONSE_PURPOSE", "text": "INJECTED"}]},
])
def test_invalid_report_question_shapes_cannot_generate_a_creation_button(invalid):
    from test_telegram_report_presentation import _clarification_report
    case, report = uuid4(), uuid4()
    canonical = _clarification_report() | invalid
    canonical["reportId"] = str(report)
    canonical["case"]["id"] = str(case)
    result = {"analysisAllowed": False, "escalationRequired": False,
              "report": {"id": str(report), "caseId": str(case), "reportJson": canonical}}
    _text, keyboard = job_message(JobSnapshot(uuid4(), case, "SUCCEEDED", None, result))
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert not any(value.startswith("case:clarify:") for value in callbacks)


@pytest.mark.parametrize("field", ["id", "caseId", "reportId", "canonicalCaseId"])
def test_foreign_report_envelope_cannot_bind_another_job(field):
    from test_telegram_report_presentation import _clarification_report
    case, report = uuid4(), uuid4()
    canonical = _clarification_report()
    canonical["reportId"] = str(report)
    canonical["case"]["id"] = str(case)
    response = {"id": str(report), "caseId": str(case), "reportJson": canonical}
    if field in {"id", "caseId"}:
        response[field] = str(uuid4())
    elif field == "reportId":
        canonical[field] = str(uuid4())
    else:
        canonical["case"]["id"] = str(uuid4())
    result = {"analysisAllowed": False, "escalationRequired": False, "report": response}
    with pytest.raises(ValueError, match="identity mismatch"):
        job_message(JobSnapshot(uuid4(), case, "SUCCEEDED", None, result))


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="isolated PostgreSQL")
def test_explicit_preview_selects_old_report_but_first_stale_creation_fails(protected_database):
    actor = 89_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    with application_client() as core:
        workflow = _confirm(core, actor, _guided_data())
        case = workflow["case"]["id"]
        original = _append_report(actor, workflow)
        latest = _append_report(actor, workflow)
        before = _source_rows(case)
        selected = core.get(f"/v1/cases/{case}/clarification-drafts/preview"
                            f"?report_id={original['id']}", headers=actor_headers(actor))
        assert selected.status_code == 200, selected.text
        assert selected.json()["expectedReportId"] == original["id"]
        current = core.get(f"/v1/cases/{case}/clarification-drafts/preview",
                           headers=actor_headers(actor)).json()
        assert current["expectedReportId"] == latest["id"]
        binding = {k: v for k, v in selected.json().items() if k != "caseId"}
        rejected = core.post(f"/v1/cases/{case}/clarification-drafts", json=binding,
                             headers=actor_headers(actor, uuid4()))
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "CASE_REPORT_STALE"
        items = core.get("/v1/telegram-intake-drafts", headers=actor_headers(actor)).json()["items"]
        assert items == []
        assert _source_rows(case) == before


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="isolated PostgreSQL")
def test_explicit_report_selector_cannot_cross_case_creator_or_clinic(protected_database):
    actor = 90_000_000_000 + uuid4().int % 100_000_000
    foreign = 91_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    seed_admin(foreign)
    with application_client() as core:
        own = _confirm(core, actor, _guided_data())
        same_creator_other_case = _confirm(core, actor, _guided_data())
        foreign_workflow = _confirm(core, foreign, _guided_data())
        case = own["case"]["id"]
        before = _source_rows(case)
        for report_id in (same_creator_other_case["report"]["id"],
                          foreign_workflow["report"]["id"], str(uuid4())):
            response = core.get(f"/v1/cases/{case}/clarification-drafts/preview"
                                f"?report_id={report_id}", headers=actor_headers(actor))
            assert response.status_code == 409, response.text
            assert "expectedReportId" not in response.json()
        response = core.get(f"/v1/cases/{case}/clarification-drafts/preview"
                            f"?report_id={own['report']['id']}", headers=actor_headers(foreign))
        assert response.status_code == 404
        assert _source_rows(case) == before


def test_same_button_after_lost_reply_and_new_report_reuses_original_draft():
    update, context, client, message = clarification_context()
    persisted = {}
    original_preview = deepcopy(PREVIEW)
    latest_preview = deepcopy(PREVIEW)

    async def preview(case_id, actor, *, source_report_id=None):
        return deepcopy(original_preview if source_report_id == UUID(
            original_preview["expectedReportId"]) else latest_preview)

    client.clarification_preview.side_effect = preview

    async def create(case_id, actor, **binding):
        key = binding["key"]
        if key not in persisted:
            persisted[key] = {
                "id": str(uuid4()), "revision": 1, "wizardState": "SAFETY",
                "draftData": {"intakeVersion": 2, "correctionSource": {
                    "caseId": str(case_id), "factSnapshotSha256": binding["expected_sha256"],
                    "reportId": str(binding["expected_report_id"]),
                    "reportSha256": binding["expected_report_sha256"],
                }},
            }
        return deepcopy(persisted[key])

    client.create_clarification_draft.side_effect = create
    message.reply_text.side_effect = RuntimeError("synthetic lost Telegram reply")
    with pytest.raises(RuntimeError, match="lost Telegram reply"):
        asyncio.run(create_clarification(update, context))
    assert len(persisted) == 1
    context.user_data = {}
    latest_preview = PREVIEW | {
        "expectedReportId": str(uuid4()), "expectedReportSha256": "c" * 64,
    }
    message.reply_text.side_effect = None
    open_clarification(update, context)
    assert len(persisted) == 1, "same original button created a second retained draft"
    assert client.create_clarification_draft.await_args_list[0] == (
        client.create_clarification_draft.await_args_list[1])


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="isolated PostgreSQL")
def test_same_button_retry_does_not_create_two_real_core_drafts(protected_database):
    actor = 88_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    with application_client() as core:
        workflow = _confirm(core, actor, _guided_data())
        case_id = workflow["case"]["id"]
        original_report = _append_report(actor, workflow)
        update, context, client, message = clarification_context()
        update.effective_user.id = actor
        _, keyboard = job_message(JobSnapshot(uuid4(), UUID(case_id), "SUCCEEDED", None, {
            "analysisAllowed": False, "escalationRequired": False, "report": original_report,
        }))
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        update.callback_query.data = next(value for value in callbacks
                                          if value.startswith("case:clarify:"))
        assert update.callback_query.data == bound_callback(case_id, original_report["id"])

        async def preview(case, user, *, source_report_id=None):
            query = "" if source_report_id is None else f"?report_id={source_report_id}"
            response = core.get(f"/v1/cases/{case}/clarification-drafts/preview{query}",
                                headers=actor_headers(user))
            assert response.status_code == 200, response.text
            return response.json()

        async def create(case, user, **binding):
            response = core.post(f"/v1/cases/{case}/clarification-drafts",
                headers=actor_headers(user, binding["key"]), json={
                    "expectedFactSnapshotSha256": binding["expected_sha256"],
                    "expectedReportId": str(binding["expected_report_id"]),
                    "expectedReportSha256": binding["expected_report_sha256"],
                })
            assert response.status_code in {200, 201}, response.text
            return response.json()

        client.clarification_preview.side_effect = preview
        client.create_clarification_draft.side_effect = create
        message.reply_text.side_effect = RuntimeError("synthetic lost Telegram reply")
        with pytest.raises(RuntimeError, match="lost Telegram reply"):
            asyncio.run(create_clarification(update, context))
        latest_report = _append_report(actor, workflow)
        before = _source_rows(case_id)
        context.user_data = {}
        message.reply_text.side_effect = None
        open_clarification(update, context)
        items = core.get("/v1/telegram-intake-drafts", headers=actor_headers(actor)).json()["items"]
        assert len(items) == 1, "same original button persisted two real Core drafts"
        saved = core.get(f"/v1/telegram-intake-drafts/{items[0]['id']}",
                         headers=actor_headers(actor)).json()
        assert saved["draftData"]["correctionSource"]["reportId"] == original_report["id"]
        assert _source_rows(case_id) == before
        first, retry = client.create_clarification_draft.await_args_list
        assert first == retry
        # A separate displayed-report button is a new intent even on the same message.
        update.callback_query.data = bound_callback(case_id, latest_report["id"])
        open_clarification(update, context)
        newer = core.get("/v1/telegram-intake-drafts", headers=actor_headers(actor)).json()["items"]
        assert len(newer) == 2
        payloads = [core.get(f"/v1/telegram-intake-drafts/{item['id']}",
                            headers=actor_headers(actor)).json() for item in newer]
        assert {item["draftData"]["correctionSource"]["reportId"] for item in payloads} == {
            original_report["id"], latest_report["id"],
        }
        assert _source_rows(case_id) == before


@pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1", reason="isolated PostgreSQL")
@pytest.mark.parametrize("expired", ["case", "draft"])
def test_old_report_selector_and_replay_preserve_expiry_boundaries(protected_database, expired):
    actor = 93_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    with application_client() as core:
        workflow = _confirm(core, actor, _guided_data())
        case_id = workflow["case"]["id"]
        report = _append_report(actor, workflow)
        path = f"/v1/cases/{case_id}/clarification-drafts"
        binding = core.get(path + "/preview", headers=actor_headers(actor)).json()
        payload = {k: v for k, v in binding.items() if k != "caseId"}
        key = uuid4()
        created = core.post(path, headers=actor_headers(actor, key), json=payload)
        assert created.status_code == 201, created.text
        _append_report(actor, workflow)
        engine = create_engine(owner_database_url())
        try:
            with engine.begin() as connection:
                if expired == "case":
                    connection.execute(text("UPDATE cases SET retention_due_at=now()-interval "
                                            "'1 second' WHERE id=:id"), {"id": case_id})
                else:
                    connection.execute(text("UPDATE telegram_intake_drafts SET "
                                            "purge_after=now()-interval '1 second' WHERE id=:id"),
                                       {"id": created.json()["id"]})
        finally:
            engine.dispose()
        before = _source_rows(case_id)
        preview = core.get(path + f"/preview?report_id={report['id']}",
                           headers=actor_headers(actor))
        assert preview.status_code == (410 if expired == "case" else 200), preview.text
        replay = core.post(path, headers=actor_headers(actor, key), json=payload)
        assert replay.status_code == 410, replay.text
        assert replay.json()["error"]["code"] == (
            "CASE_CONTENT_EXPIRED" if expired == "case" else "INTAKE_DRAFT_EXPIRED")
        assert _source_rows(case_id) == before
