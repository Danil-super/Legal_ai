"""The legacy report adapter must not append controls owned by a versioned reader."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from telegram_gateway import analysis_runtime, bot


@pytest.mark.parametrize("versioned", [False, True])
@pytest.mark.parametrize("urgent", [False, True])
def test_report_adapter_leaves_versioned_navigation_to_its_original_reader(
    monkeypatch, versioned, urgent,
):
    original = AsyncMock()
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message)
    client = SimpleNamespace()
    workflow = {"case": {"id": str(uuid4()),
                         "earlyEscalationId": str(uuid4()) if urgent else None}}
    if versioned:
        workflow["receiptVersion"] = 2
    monkeypatch.setattr(bot, "_send_workflow_report", original)
    monkeypatch.setattr(analysis_runtime, "_PATCHED", False)
    analysis_runtime._install_report_analysis_button()
    asyncio.run(bot._send_workflow_report(update, client, workflow, 7000000001))
    original.assert_awaited_once_with(update, client, workflow, 7000000001)
    assert message.reply_text.await_count == (0 if versioned else 1)
