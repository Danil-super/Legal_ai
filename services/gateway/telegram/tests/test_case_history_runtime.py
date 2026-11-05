"""Compact history navigation and Telegram callback-size boundaries."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from telegram.ext import ApplicationHandlerStop
from telegram_gateway.case_history_runtime import _date, _pdf_callback, history_callback
from telegram_gateway.clarification_intent import decode_clarification_callback


@pytest.mark.parametrize("action", ["list", "view"])
def test_history_has_compact_text_and_buttons_without_report_contents(monkeypatch, action):
    case, report = uuid4(), uuid4()
    card = {"id": str(case), "publicNumber": "DL-2026-000034", "status": "REPORT_READY",
            "intakeSchemaVersion": "dental-case-intake.v2", "createdAt": "2026-11-05T12:00:00Z"}
    page = {"items": [card], "nextOffset": 8} if action == "list" else {
        "case": card, "reports": [{"id": str(report), "reportVersion": 2,
                                   "createdAt": "2026-11-05T12:00:00Z", "kind": "READY"}],
    }
    core = SimpleNamespace(_json_request=AsyncMock(return_value=page))
    reply = AsyncMock()
    monkeypatch.setattr("telegram_gateway.bot._legal_core", lambda context: core)
    monkeypatch.setattr("telegram_gateway.bot._reply", reply)
    data = "history:list:0" if action == "list" else f"history:view:{case}:0"
    update = SimpleNamespace(effective_user=SimpleNamespace(id=777),
                             callback_query=SimpleNamespace(data=data, answer=AsyncMock()))
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(history_callback(update, SimpleNamespace()))
    assert len(reply.await_args.args[1]) < 200
    buttons = [b for row in reply.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert all(len(b.callback_data.encode()) <= 64 for b in buttons)
    if action == "list":
        assert any(b.callback_data == f"history:view:{case}:0" for b in buttons)
        row = reply.await_args.kwargs["reply_markup"].inline_keyboard[0]
        assert len(row) == 2 and row[1].callback_data == f"history:delete:{case}:0"
    else:
        assert any(b.callback_data == _pdf_callback(case, report, 0) for b in buttons)
    assert core._json_request.await_args.kwargs["telegram_user_id"] == 777


def test_historical_pdf_pointer_roundtrips_two_ids_within_telegram_limit():
    case, report = uuid4(), uuid4()
    callback = _pdf_callback(case, report, 100000)
    assert len(callback.encode()) <= 64
    pair = callback.split(":")[2]
    assert decode_clarification_callback("case:clarify:" + pair) == (case, report)


def test_history_dates_follow_moscow_day_instead_of_the_server_utc_day():
    assert _date(datetime.fromisoformat("2026-11-05T22:30:00+00:00")) == "06.11.2026"


def test_delete_confirmation_names_case_and_cancel_does_not_mutate(monkeypatch):
    case = uuid4()
    card = {"id": str(case), "publicNumber": "DL-2026-000034", "status": "REPORT_READY",
            "intakeSchemaVersion": "dental-case-intake.v2", "createdAt": "2026-11-05T12:00:00Z"}
    core = SimpleNamespace(_json_request=AsyncMock(side_effect=[
        {"case": card, "reports": []}, {"items": [card]},
    ]))
    reply = AsyncMock()
    monkeypatch.setattr("telegram_gateway.bot._legal_core", lambda context: core)
    monkeypatch.setattr("telegram_gateway.bot._reply", reply)

    async def click(data):
        update = SimpleNamespace(effective_user=SimpleNamespace(id=777),
                                 callback_query=SimpleNamespace(data=data, answer=AsyncMock()))
        with pytest.raises(ApplicationHandlerStop):
            await history_callback(update, SimpleNamespace())

    asyncio.run(click(f"history:delete:{case}:8"))
    assert "DL-2026-000034" in reply.await_args.args[1]
    rows = reply.await_args.kwargs["reply_markup"].inline_keyboard
    assert rows[0][0].callback_data == f"history:remove:{case}:8"
    assert rows[1][0].callback_data == "history:list:8"
    asyncio.run(click("history:list:8"))
    assert all(call.args[0] == "GET" for call in core._json_request.await_args_list)


def test_confirm_delete_reauthorizes_and_returns_to_valid_list_page(monkeypatch):
    case = uuid4()
    core = SimpleNamespace(_json_request=AsyncMock(side_effect=[
        {"caseId": str(case), "status": "DELETION_SCHEDULED"},
        {"items": []}, {"items": []},
    ]))
    reply = AsyncMock()
    monkeypatch.setattr("telegram_gateway.bot._legal_core", lambda context: core)
    monkeypatch.setattr("telegram_gateway.bot._reply", reply)
    update = SimpleNamespace(effective_user=SimpleNamespace(id=777), callback_query=SimpleNamespace(
        data=f"history:remove:{case}:8", answer=AsyncMock(),
    ))
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(history_callback(update, SimpleNamespace()))
    calls = core._json_request.await_args_list
    assert calls[0].args == ("DELETE", f"/v1/case-history/{case}")
    assert calls[0].kwargs["telegram_user_id"] == 777
    assert calls[-1].args[1] == "/v1/case-history?offset=0"
    assert "Кейс удалён" in reply.await_args.args[1]


def test_delete_timeout_never_claims_success_and_keeps_safe_retry(monkeypatch):
    from telegram_gateway.case_wizard import LegalCoreApiError

    case = uuid4()
    core = SimpleNamespace(_json_request=AsyncMock(side_effect=LegalCoreApiError(
        503, "LEGAL_CORE_UNAVAILABLE", "unavailable",
    )))
    reply = AsyncMock()
    monkeypatch.setattr("telegram_gateway.bot._legal_core", lambda context: core)
    monkeypatch.setattr("telegram_gateway.bot._reply", reply)
    update = SimpleNamespace(effective_user=SimpleNamespace(id=777), callback_query=SimpleNamespace(
        data=f"history:remove:{case}:0", answer=AsyncMock(),
    ))
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(history_callback(update, SimpleNamespace()))
    assert "Кейс удалён" not in reply.await_args.args[1]
    buttons = reply.await_args.kwargs["reply_markup"].inline_keyboard
    assert any(b.callback_data == f"history:remove:{case}:0" for row in buttons for b in row)
