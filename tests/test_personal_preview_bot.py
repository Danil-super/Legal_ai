"""Real PTB dispatch with fake Telegram transport; no tokens or live API calls."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from telegram import Update
from telegram.ext import ExtBot

from telegram_gateway.personal_preview import build_application


def preview_env():
    return {
        "PERSONAL_PREVIEW_MODE": "synthetic", "PERSONAL_PREVIEW_TESTER_IDS": "101",
        "PERSONAL_PREVIEW_API_KEY": "preview_" + "p" * 40,
        "PERSONAL_TELEGRAM_BOT_TOKEN": "987654:" + "p" * 40,
        "CLINIC_TELEGRAM_BOT_ID": "123456",
    }


@asynccontextmanager
async def application(monkeypatch):
    calls = []

    async def fake_post(self, endpoint, data=None, **kwargs):
        values = dict(data or {})
        calls.append((endpoint, values))
        if endpoint == "getMe":
            return {"id": 987654, "is_bot": True, "first_name": "Preview", "username": "preview"}
        if endpoint == "answerCallbackQuery":
            return True
        assert endpoint in {"sendMessage", "editMessageText"}
        return {"message_id": values.get("message_id", 10), "date": 1,
                "chat": {"id": values.get("chat_id", 101), "type": "private"},
                "text": values.get("text", "")}

    monkeypatch.setattr(ExtBot, "_post", fake_post)
    app = build_application(preview_env())
    assert app is not None
    await app.initialize()
    try:
        yield app, calls
    finally:
        await app.shutdown()


def update_for(app, *, actor=101, chat_type="private", callback=None, document=False,
               command=True):
    user = {"id": actor, "is_bot": False, "first_name": "Synthetic"}
    message = {"message_id": 1, "date": 1, "from": user,
               "chat": {"id": actor, "type": chat_type}, "text": "/start"}
    if command:
        message["entities"] = [{"type": "bot_command", "offset": 0, "length": 6}]
    else:
        message["text"] = "SYNTHETIC_PRIVATE_CONTENT"
    if document:
        message.pop("text", None)
        message.pop("entities", None)
        message["document"] = {"file_id": "SYNTHETIC_PRIVATE_FILE", "file_unique_id": "fake",
                               "file_name": "synthetic_personal_record.pdf"}
    body = {"update_id": 1}
    if callback is None:
        body["message"] = message
    else:
        body["callback_query"] = {"id": "1", "chat_instance": "synthetic", "from": user,
                                  "message": message, "data": callback}
    return Update.de_json(body, app.bot)


def test_off_never_builds_a_bot_or_requires_token():
    assert build_application({}) is None
    assert build_application({"PERSONAL_PREVIEW_MODE": "off", "TELEGRAM_BOT_TOKEN": "x"}) is None


@pytest.mark.parametrize("callback", [None, "pp:audience:PATIENT", "pp:audience:EMPLOYEE",
                                      "pp:demo:patient_documents", "pp:demo:employee_pay"])
def test_allowlisted_navigation_has_no_live_answer(monkeypatch, callback):
    async def scenario():
        async with application(monkeypatch) as (app, calls):
            await app.process_update(update_for(app, callback=callback))
            texts = [data.get("text", "") for _, data in calls]
            assert any("прототип" in text.lower() for text in texts)
            assert not app.bot_data
            assert all(not value for value in app.user_data.values())
            assert all(not value for value in app.chat_data.values())
    asyncio.run(scenario())


@pytest.mark.parametrize("actor,chat_type", [(102, "private"), (101, "group"), (101, "supergroup")])
@pytest.mark.parametrize("callback", [None, "pp:audience:PATIENT", "pp:demo:employee_pay"])
def test_unauthorized_and_shared_chats_never_receive_preview(
    monkeypatch, actor, chat_type, callback,
):
    async def scenario():
        async with application(monkeypatch) as (app, calls):
            calls.clear()
            await app.process_update(update_for(app, actor=actor, chat_type=chat_type,
                                                 callback=callback))
            assert not calls
    asyncio.run(scenario())


@pytest.mark.parametrize("document", [False, True])
def test_text_and_files_are_never_fetched_saved_or_echoed(monkeypatch, document, caplog):
    async def scenario():
        async with application(monkeypatch) as (app, calls):
            await app.process_update(update_for(app, command=False, document=document))
            assert all(method != "getFile" for method, _ in calls)
            assert "SYNTHETIC_PRIVATE" not in str(calls)
            assert any("ещё закрыты" in data.get("text", "") for _, data in calls)
            assert all(not value for value in app.user_data.values())
            assert "SYNTHETIC_PRIVATE" not in caplog.text
    asyncio.run(scenario())
