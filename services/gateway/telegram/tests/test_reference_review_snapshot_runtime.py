"""Actual PTB and HTTP transport: review the displayed source, never its replacement."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import httpx2
import pytest
from telegram import Update
from telegram.ext import Application, ExtBot
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreClient
from telegram_gateway.reference_evaluation_runtime import PENDING_KEY, install_reference_evaluations

ACTOR = 7_000_000_001
CASE = UUID("4d8d752e-f32c-4dc5-8f74-40bff8e80d5b")


@asynccontextmanager
async def gateway(monkeypatch):
    state = SimpleNamespace(snapshot="a" * 64, version=1, status="READY_FOR_REVIEW",
                            can_review=True, fail_once=False, lost_once=False)
    requests, sent, errors = [], [], []

    async def telegram_post(self, endpoint, data=None, **kwargs):
        data = dict(data or {})
        if endpoint == "getMe":
            return {"id": 123456, "is_bot": True, "first_name": "Synthetic",
                    "username": "synthetic_bot"}
        if endpoint == "answerCallbackQuery":
            return True
        sent.append(data)
        return {"message_id": 100 + len(sent), "date": 1,
                "chat": {"id": data.get("chat_id", ACTOR), "type": "private"},
                "text": data.get("text", "")}

    def core_request(request):
        requests.append(request)
        if request.url.path.endswith("/access"):
            return httpx2.Response(200, json={"canContribute": False,
                                             "canReview": state.can_review})
        if request.method == "GET":
            return httpx2.Response(200, json={
                "id": str(CASE), "status": state.status, "currentVersion": state.version,
                "reviewSnapshotSha256": state.snapshot, "createdBySelf": False,
                "displayName": "Вымышленный пример", "hasMaterial": False,
                "scenarioText": "Пломба выпала, клиника пригласила на осмотр.",
                "groupKey": "healthcare", "asOfDate": "2026-10-08",
                "expectedRoute": "INTERNAL_DRAFT",
            })
        assert request.url.path == f"/v1/reference-evaluations/{CASE}/review"
        body = json.loads(request.content)
        if state.fail_once:
            state.fail_once = False
            return httpx2.Response(503, json={"error": {"code": "SERVICE_UNAVAILABLE",
                                                        "message": "Synthetic unavailable"}})
        if body.get("expectedReviewSnapshotSha256") != state.snapshot:
            return httpx2.Response(409, json={"error": {"code": "REFERENCE_REVIEW_SOURCE_STALE",
                                                        "message": "Synthetic stale source"}})
        state.status = {"APPROVE_FOR_EVALUATION": "APPROVED_FOR_EVALUATION",
                        "REJECT": "REJECTED", "CHANGES_REQUIRED": "CHANGES_REQUIRED"}[
                            body["decision"]]
        if state.lost_once:
            state.lost_once = False
            raise httpx2.ReadTimeout("Synthetic lost response", request=request)
        return httpx2.Response(200, json={"case": {"id": str(CASE), "status": state.status}})

    monkeypatch.setattr(ExtBot, "_post", telegram_post)
    async with httpx2.AsyncClient(base_url="http://core.test",
                                  transport=httpx2.MockTransport(core_request)) as http:
        app = Application.builder().token("123456:unit_test_token_value_1234567890").build()
        app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = LegalCoreClient(http)
        install_reference_evaluations(app)

        async def error_handler(update, context):
            errors.append(context.error)

        app.add_error_handler(error_handler)
        await app.initialize()
        counter = 0

        async def dispatch(value, *, actor=ACTOR, text=False):
            nonlocal counter
            counter += 1
            user = {"id": actor, "is_bot": False, "first_name": "Synthetic"}
            message = {"message_id": counter, "date": 1, "from": user,
                       "chat": {"id": actor, "type": "private"}}
            payload = {"update_id": counter}
            if text:
                message["text"] = value
                payload["message"] = message
            else:
                payload["callback_query"] = {"id": str(counter), "chat_instance": "synthetic",
                                              "from": user, "message": message, "data": value}
            await app.process_update(Update.de_json(payload, app.bot))
            assert not errors, errors

        def button(action):
            for item in reversed(sent):
                markup = item.get("reply_markup")
                if markup is not None:
                    for row in markup.inline_keyboard:
                        for control in row:
                            if str(control.callback_data).startswith(f"refeval:{action}:{CASE}"):
                                return str(control.callback_data)
            raise AssertionError(f"No displayed {action} control")

        def posts():
            return [request for request in requests if request.method == "POST"]

        try:
            yield SimpleNamespace(state=state, app=app, requests=requests, sent=sent,
                                  dispatch=dispatch, button=button, posts=posts)
        finally:
            await app.shutdown()


@pytest.mark.parametrize("same_version", [False, True])
def test_old_display_cannot_approve_revised_scenario_or_replaced_raw_material(monkeypatch,
                                                                          same_version):
    async def run():
        async with gateway(monkeypatch) as h:
            await h.dispatch(f"refeval:view:{CASE}")
            old = h.button("approve")
            assert len(old.encode()) <= 64 and len(old.rsplit(":", 1)[1]) == 10
            h.state.snapshot = "b" * 64
            h.state.version = 1 if same_version else 2
            await h.dispatch(old)
            assert h.state.status == "READY_FOR_REVIEW"
            assert len(h.posts()) == 1
            assert json.loads(h.posts()[0].content)["expectedReviewSnapshotSha256"] == "a" * 64
            fresh = h.button("approve")
            assert fresh != old
            await h.dispatch(fresh)
            await h.dispatch(fresh)
            bodies = [json.loads(request.content) for request in h.posts()]
            assert [body["expectedReviewSnapshotSha256"] for body in bodies] == [
                "a" * 64, "b" * 64, "b" * 64]
            keys = [request.headers["idempotency-key"] for request in h.posts()]
            assert keys[0] != keys[1] == keys[2]
            assert h.state.status == "APPROVED_FOR_EVALUATION"
    asyncio.run(run())


def test_missing_old_evicted_or_other_users_nonce_only_refreshes_without_post(monkeypatch):
    async def run():
        async with gateway(monkeypatch) as h:
            await h.dispatch(f"refeval:view:{CASE}")
            first = h.button("approve")
            for decision in ("approve", "reject", "changes"):
                for action in (f"refeval:{decision}:{CASE}",
                               f"refeval:{decision}:{CASE}:0000000000"):
                    await h.dispatch(action)
            await h.dispatch(first, actor=ACTOR + 1)
            for _ in range(35):
                await h.dispatch(f"refeval:view:{CASE}")
            await h.dispatch(first)
            assert h.posts() == []
            assert PENDING_KEY not in h.app.user_data[ACTOR]
    asyncio.run(run())


def test_changes_note_keeps_displayed_snapshot_when_source_changes_before_text(monkeypatch):
    async def run():
        async with gateway(monkeypatch) as h:
            await h.dispatch(f"refeval:view:{CASE}")
            await h.dispatch(h.button("changes"))
            pending = h.app.user_data[ACTOR][PENDING_KEY]
            assert pending["reviewSnapshotSha256"] == "a" * 64
            h.state.snapshot, h.state.version = "b" * 64, 2
            await h.dispatch("Уточните хронологию действий клиники.", text=True)
            assert len(h.posts()) == 1
            body = json.loads(h.posts()[0].content)
            assert body["decision"] == "CHANGES_REQUIRED"
            assert body["expectedReviewSnapshotSha256"] == "a" * 64
            assert h.state.status == "READY_FOR_REVIEW"
            assert PENDING_KEY not in h.app.user_data[ACTOR]
    asyncio.run(run())


@pytest.mark.parametrize("decision", ["approve", "reject", "changes"])
def test_lost_or_failed_reply_retry_keeps_display_token_and_action_key(monkeypatch, decision):
    async def run():
        async with gateway(monkeypatch) as h:
            await h.dispatch(f"refeval:view:{CASE}")
            action = h.button(decision)
            h.state.lost_once = True
            await h.dispatch(action)
            if decision == "changes":
                await h.dispatch("Уточните хронологию действий клиники.", text=True)
                await h.dispatch("Уточните хронологию действий клиники.", text=True)
            else:
                await h.dispatch(action)
            assert len(h.posts()) == 2
            assert h.posts()[0].content == h.posts()[1].content
            assert (h.posts()[0].headers["idempotency-key"]
                    == h.posts()[1].headers["idempotency-key"])
            assert json.loads(h.posts()[0].content)["expectedReviewSnapshotSha256"] == "a" * 64
    asyncio.run(run())


@pytest.mark.parametrize("snapshot", [None, "a" * 64])
def test_review_client_optional_snapshot_preserves_absent_legacy_wire(snapshot):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json={"case": {"id": str(CASE)}})

    async def run():
        async with httpx2.AsyncClient(base_url="http://core.test",
                                      transport=httpx2.MockTransport(handler)) as http:
            await LegalCoreClient(http).review_reference_evaluation(
                CASE, ACTOR, decision="APPROVE_FOR_EVALUATION", note=None,
                idempotency_key=CASE, expected_review_snapshot_sha256=snapshot,
            )

    asyncio.run(run())
    expected = {"decision": "APPROVE_FOR_EVALUATION"}
    if snapshot is not None:
        expected["expectedReviewSnapshotSha256"] = snapshot
    assert json.loads(requests[0].content) == expected
    assert requests[0].headers["x-telegram-user-id"] == str(ACTOR)
    assert "x-clinic-id" not in requests[0].headers
