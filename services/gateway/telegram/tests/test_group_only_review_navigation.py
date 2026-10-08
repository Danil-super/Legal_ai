"""Document cards are read-only; only explicit group actions can approve."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from telegram.ext import ApplicationHandlerStop
from telegram_gateway import legal_library_runtime as runtime


@pytest.mark.parametrize(
    "action", ["confirm", "attest:source", "attest:artifact", "attest:dates", "attest:fragments"]
)
def test_old_single_document_buttons_never_approve_or_toggle_state(monkeypatch, action):
    version = UUID(int=23)
    prefix, _, key = action.partition(":")
    callback = f"editor:{prefix}:{version}" + (f":{key}" if key else "")
    pending = {
        "versionId": str(version),
        "confirmationEnabled": True,
        "attestations": dict.fromkeys(("source", "artifact", "dates", "fragments"), True),
        "expected": {"expectedSha256": "a" * 64},
        "idempotencyKey": str(UUID(int=24)),
    }
    context = SimpleNamespace(user_data={runtime._EDITOR_PENDING_KEY: pending})
    client = SimpleNamespace(
        approve_editor_version=AsyncMock(), get_editor_version=AsyncMock(), aclose=AsyncMock()
    )
    reply = AsyncMock()
    monkeypatch.setattr(runtime, "LegalLibraryClient", lambda: client)
    monkeypatch.setattr(runtime.gateway_bot, "_answer_callback", AsyncMock(return_value=callback))
    monkeypatch.setattr(runtime.gateway_bot, "_actor_id", lambda _: 1)
    monkeypatch.setattr(runtime.gateway_bot, "_reply", reply)
    monkeypatch.setattr(runtime, "_editor_reply", reply)

    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(runtime.legal_editor_callback(SimpleNamespace(), context))

    client.approve_editor_version.assert_not_awaited()
    client.get_editor_version.assert_not_awaited()
    assert runtime._EDITOR_PENDING_KEY not in context.user_data
    assert "групп" in reply.await_args.args[1].lower()
    keyboard = reply.await_args.args[2]
    assert "editor:materials:1" in [
        button.callback_data for row in keyboard.inline_keyboard for button in row
    ]


def test_reference_card_uses_the_same_open_document_action_and_main_navigation():
    version = UUID(int=23)
    _, keyboard = runtime.render_material_preparation(
        {
            "materialId": str(version),
            "groupKey": "clinical",
            "kind": "CLINICAL_REFERENCE",
            "title": "Synthetic reference",
            "limitations": [],
            "missingFields": [],
        }
    )
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert buttons[0].text == "📄 Открыть документ"
    assert buttons[0].callback_data == f"editor:material:{version}"
    assert buttons[-1].callback_data == "menu"


@pytest.mark.parametrize(
    "group", ["clinical", "labour", "courts", "privacy", "licensing", "healthcare", "general"]
)
def test_group_list_has_one_uniform_open_button_per_prepared_document(group):
    version, material = str(UUID(int=23)), str(UUID(int=24))
    _, keyboard = runtime.render_editor_review_materials(
        {
            "page": 1,
            "pageSize": 10,
            "totalItems": 2,
            "selectedGroup": group,
            "referenceReviewableCount": 1,
            "groups": [],
            "items": [
                {"versionId": version, "materialId": material, "title": "Synthetic norm"},
                {
                    "materialId": material,
                    "preparationId": material,
                    "title": "Synthetic reference",
                    "kind": "CLINICAL_REFERENCE",
                },
            ],
        }
    )
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    document_buttons = [button for button in buttons if button.text.startswith("📄 Открыть: ")]
    assert len(document_buttons) == 2
    assert document_buttons[0].callback_data == f"editor:detail:{version}:1"
    assert document_buttons[1].callback_data == f"editor:preparation:{material}"
    assert not any(button.callback_data == f"editor:material:{material}" for button in buttons)
    assert any(button.callback_data == f"editor:refbatch:{group}" for button in buttons)
    assert any(button.callback_data == f"editor:batch:{group}" for button in buttons) is (
        group != "clinical"
    )


def test_approved_document_card_does_not_claim_approval_is_unavailable():
    text, keyboard = runtime.render_editor_version_detail(
        {
            "versionId": str(UUID(int=23)),
            "documentTitle": "Synthetic norm",
            "approvalState": "APPROVED",
            "approvalEligible": False,
        },
        {},
    )
    assert "Документ утверждён" in text
    assert "не может быть одобрена" not in text
    assert not any(
        (button.callback_data or "").startswith(("editor:attest:", "editor:confirm:"))
        for row in keyboard.inline_keyboard
        for button in row
    )
