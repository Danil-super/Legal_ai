"""Lost responses and concurrent retries retain one actual PostgreSQL operation."""

import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx2
import pytest
from telegram.ext import ApplicationHandlerStop
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from test_case_api import actor_headers, application_client, seed_admin
from test_lawyer_workspace import seed_escalation
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


@pytest.fixture
def operation_case(isolated_database):
    actor, escalation, clinic = seed_escalation()
    with application_client() as client:
        path = f"/v1/case-escalations/{escalation}"
        assert client.post(path + "/claim", headers=actor_headers(actor)).status_code == 200
        yield client, actor, path, clinic


@pytest.mark.parametrize("action", ["discussion", "resolve"])
def test_committed_operation_can_be_reconciled_after_its_response_is_lost(operation_case, action):
    client, actor, path, _ = operation_case
    key = uuid4()
    headers = actor_headers(actor, key)
    body = {"body": "Синтетическое сообщение специалиста."}
    first = client.post(path + "/" + action, headers=headers, json=body)
    assert first.status_code == (201 if action == "discussion" else 200), first.text
    # The caller did not receive first: replay the same durable intent.
    replay = client.post(path + "/" + action, headers=headers, json=body)
    assert replay.status_code == first.status_code, replay.text
    assert replay.json() == first.json()
    messages = client.get(path + "/discussion", headers=actor_headers(actor)).json()["items"]
    assert len(messages) == 1
    changed = client.post(path + "/" + action, headers=headers,
                          json={"body": "Другое синтетическое сообщение."})
    assert changed.status_code == 422, changed.text
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(client.get(path + "/discussion", headers=actor_headers(actor)).json()["items"]) == 1


@pytest.mark.parametrize("action", ["discussion", "resolve"])
def test_concurrent_operation_requests_commit_exactly_once(operation_case, action):
    client, actor, path, _ = operation_case
    headers = actor_headers(actor, uuid4())
    body = {"body": "Синтетическое параллельное сообщение."}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(path + "/" + action,
                              headers=headers, json=body), range(2)))
    assert [r.status_code for r in responses] == ([201, 201] if action == "discussion"
                                                 else [200, 200])
    assert responses[0].json() == responses[1].json()
    assert len(client.get(path + "/discussion", headers=actor_headers(actor)).json()["items"]) == 1


@pytest.mark.parametrize("action", ["discussion", "resolve"])
@pytest.mark.parametrize("change", ["revoked", "foreign", "expired"])
def test_replay_requires_current_actor_tenant_and_retention(operation_case, action, change):
    client, actor, path, clinic = operation_case
    headers = actor_headers(actor, uuid4())
    body = {"body": "Синтетическая проверка доступа."}
    assert client.post(path + "/" + action, headers=headers, json=body).status_code in (200, 201)
    if change == "foreign":
        other = actor + 20_000_000_000
        seed_admin(other, role="CLINIC_OWNER")
        headers["X-Telegram-User-Id"] = str(other)
    else:
        owner = create_engine(owner_database_url())
        try:
            with owner.begin() as db:
                if change == "revoked":
                    db.execute(text("UPDATE clinic_users SET status='REVOKED' WHERE clinic_id=:c"),
                               {"c": clinic})
                else:
                    db.execute(text("UPDATE cases SET retention_due_at=clock_timestamp()"
                                    "-interval '1 second' WHERE clinic_id=:c"), {"c": clinic})
        finally:
            owner.dispose()
    denied = client.post(path + "/" + action, headers=headers, json=body)
    assert denied.status_code == {"revoked": 403, "foreign": 404, "expired": 410}[change]


def test_malformed_operation_key_is_rejected_before_a_message_is_saved(operation_case):
    client, actor, path, _ = operation_case
    response = client.post(path + "/discussion", headers={**actor_headers(actor),
        "Idempotency-Key": "not-a-uuid"}, json={"body": "Синтетическая проверка."})
    assert response.status_code == 422
    assert client.get(path + "/discussion", headers=actor_headers(actor)).json()["items"] == []


def test_resolution_replay_does_not_authorize_another_lawyer_or_a_changed_role(operation_case):
    client, actor, path, clinic = operation_case
    key = uuid4()
    body = {"body": "Синтетическое завершение."}
    assert client.post(path + "/resolve", headers=actor_headers(actor, key),
                       json=body).status_code == 200
    lawyer = actor + 40_000_000_000
    assert client.post("/v1/clinic/members", headers=actor_headers(actor),
                       json={"telegramUserId": lawyer, "role": "CLINIC_LAWYER"}).status_code == 201
    denied = client.post(path + "/resolve", headers=actor_headers(lawyer, key), json=body)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "ASSIGNEE_REQUIRED"
    owner = create_engine(owner_database_url())
    try:
        with owner.begin() as db:
            db.execute(text("UPDATE clinic_users SET role='CLINIC_ADMIN' "
                            "WHERE clinic_id=:c AND role='CLINIC_OWNER'"), {"c": clinic})
        assert client.post(path + "/resolve", headers=actor_headers(actor, key),
                           json=body).status_code == 403
    finally:
        owner.dispose()


@pytest.mark.parametrize("action", ["discussion", "resolve"])
def test_receipt_metadata_does_not_duplicate_text_and_rolls_back_with_the_message(
    operation_case, action, monkeypatch,
):
    import legal_core.escalation_message_receipts as receipts

    client, actor, path, _ = operation_case
    key, body = uuid4(), {"body": "Синтетическое сообщение для атомарной проверки."}
    original = receipts.retain_message

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic failure before commit")

    with monkeypatch.context() as patch:
        patch.setattr(receipts, "retain_message", failed)
        with pytest.raises(RuntimeError, match="synthetic failure before commit"):
            client.post(path + "/" + action, headers=actor_headers(actor, key), json=body)
    assert client.get(path + "/discussion", headers=actor_headers(actor)).json()["items"] == []
    assert client.get(path, headers=actor_headers(actor)).json()["status"] == "IN_PROGRESS"
    result = client.post(path + "/" + action, headers=actor_headers(actor, key), json=body)
    assert result.status_code in (200, 201), result.text
    owner = create_engine(owner_database_url())
    try:
        with owner.connect() as db:
            records = db.execute(text("SELECT response_json FROM idempotency_records "
                                      "WHERE key=:key"), {"key": str(key)}).scalars().all()
            assert len(records) == 1 and set(records[0]) == {"messageId"}
            audit = db.execute(text("SELECT metadata_json FROM audit_events "
                                    "WHERE action='ESCALATION_OPERATION_RETAINED'")).scalars().all()
            assert len(audit) == 1 and body["body"] not in json.dumps([records, audit])
    finally:
        owner.dispose()


@pytest.mark.parametrize("action", ["discussion", "resolve"])
def test_access_is_rechecked_after_the_actual_case_lock_wait(operation_case, action, monkeypatch):
    from legal_core import case_api

    client, actor, path, clinic = operation_case
    started = threading.Event()
    original = case_api.resolve_actor

    async def observed(*args, **kwargs):
        result = await original(*args, **kwargs)
        started.set()
        return result

    monkeypatch.setattr(case_api, "resolve_actor", observed)
    owner = create_engine(owner_database_url())
    try:
        with owner.connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
            blocker.execute(text("SELECT id FROM cases WHERE clinic_id=:c FOR UPDATE"),
                            {"c": clinic})
            pending = pool.submit(client.post, path + "/" + action,
                                  headers=actor_headers(actor, uuid4()),
                                  json={"body": "Синтетическая проверка ожидания."})
            try:
                assert started.wait(5) and not pending.done()
                with owner.begin() as db:
                    db.execute(text("UPDATE clinic_users SET status='REVOKED' "
                                    "WHERE clinic_id=:c"), {"c": clinic})
            finally:
                blocker.commit()
            denied = pending.result(timeout=5)
            assert denied.status_code == 403, denied.text
        with owner.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM case_escalation_messages")) == 0
            assert db.scalar(text("SELECT count(*) FROM idempotency_records")) == 0
    finally:
        owner.dispose()


def test_mutable_receipt_pointer_cannot_replay_another_message(operation_case):
    client, actor, path, _ = operation_case
    key, body = uuid4(), {"body": "Синтетическая проверка указателя."}
    first = client.post(path + "/discussion", headers=actor_headers(actor, key), json=body)
    other = client.post(path + "/discussion", headers=actor_headers(actor, uuid4()), json=body)
    assert first.status_code == other.status_code == 201
    owner = create_engine(owner_database_url())
    try:
        with owner.begin() as db:
            db.execute(text("UPDATE idempotency_records SET response_json=CAST(:body AS jsonb) "
                            "WHERE key=:key"),
                       {"key": str(key), "body": json.dumps({"messageId": other.json()["id"]})})
        denied = client.post(path + "/discussion", headers=actor_headers(actor, key), json=body)
        assert denied.status_code == 409
        assert denied.json()["error"]["code"] == "ESCALATION_OPERATION_INVALID"
    finally:
        owner.dispose()


@pytest.mark.parametrize("action", ["discussion", "resolve"])
@pytest.mark.parametrize("lost_count", [1, 2])
def test_gateway_reconciles_actual_commit_after_readtimeout_and_restart(
    operation_case, action, lost_count,
):
    from telegram_gateway.analysis_runtime import post_escalation_discussion_message
    from telegram_gateway.case_wizard import LegalCoreClient
    from telegram_gateway.escalation_message_runtime import retry_message

    client, actor, path, _ = operation_case
    escalation = UUID(path.rsplit("/", 1)[1])
    attempts = []

    async def bridge(request):
        attempts.append(dict(request.headers))
        response = client.request(request.method, request.url.path,
                                  headers=dict(request.headers), content=request.content)
        assert response.status_code in (200, 201), response.text
        if len(attempts) <= lost_count:
            raise httpx2.ReadTimeout("synthetic reply lost after actual Core commit",
                                     request=request)
        return httpx2.Response(response.status_code, content=response.content,
                               headers={"content-type": "application/json"})

    async def scenario():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(bridge),
                                     base_url="http://core.test") as http:
            original = SimpleNamespace(message_id=321, text="Синтетическое заключение.",
                from_user=SimpleNamespace(id=actor), chat=SimpleNamespace(type="private", id=actor))
            replies = AsyncMock()
            update = SimpleNamespace(effective_user=SimpleNamespace(id=actor),
                effective_message=SimpleNamespace(message_id=321, text=original.text,
                                                  reply_text=replies))
            data = {"escalation_discussion_id": str(escalation)}
            if action == "resolve":
                data["escalation_resolution_pending"] = str(escalation)
            context = SimpleNamespace(user_data=data,
                                      bot_data={"legal_core_client": LegalCoreClient(http)})
            with pytest.raises(ApplicationHandlerStop):
                await post_escalation_discussion_message(update, context)
            assert len(attempts) == 2
            if lost_count == 2:
                markup = replies.call_args.kwargs["reply_markup"]
                callback = markup.inline_keyboard[0][0].callback_data
                assert len(callback.encode()) <= 64
                # A fresh gateway has no in-memory pending binding. Telegram's reply
                # retains the original private user message and the durable source ID.
                context.user_data = {}
                update.callback_query = SimpleNamespace(data=callback, answer=AsyncMock(),
                    message=SimpleNamespace(reply_to_message=original))
                with pytest.raises(ApplicationHandlerStop):
                    await retry_message(update, context)
                assert len(attempts) == 3
            assert all(item["idempotency-key"] == attempts[0]["idempotency-key"]
                       for item in attempts)
            assert "✅" in replies.call_args.args[0]

    asyncio.run(scenario())
    assert len(client.get(path + "/discussion", headers=actor_headers(actor)).json()["items"]) == 1
    if action == "resolve":
        assert client.get(path, headers=actor_headers(actor)).json()["status"] == "RESOLVED"
