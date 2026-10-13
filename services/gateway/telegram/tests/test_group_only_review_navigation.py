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


@pytest.mark.parametrize(
    "group", ["labour", "courts", "privacy", "licensing", "healthcare", "general"]
)
def test_fully_approved_group_has_no_normative_approval_action(group):
    text, keyboard = runtime.render_editor_review_materials(
        {
            "page": 1,
            "pageSize": 10,
            "totalItems": 9,
            "selectedGroup": group,
            "referenceReviewableCount": 0,
            "progress": {
                "normativeVersions": 9,
                "approvedNormativeVersions": 9,
                "referenceMaterials": 0,
                "reviewedReferenceMaterials": 0,
                "missingParts": 0,
                "unpreparedOriginals": 0,
                "unlinkedVersions": 0,
                "complete": True,
            },
            "items": [
                {
                    "versionId": str(UUID(int=index)),
                    "title": f"Synthetic approved norm {index}",
                    "reviewState": "APPROVED",
                }
                for index in range(1, 10)
            ],
        }
    )
    callbacks = [button.callback_data or "" for row in keyboard.inline_keyboard for button in row]
    assert "Группа проверена." in text
    assert not any(callback.startswith("editor:batch:") for callback in callbacks)
    assert len([callback for callback in callbacks if callback.startswith("editor:detail:")]) == 9
    assert "editor:materials:1" in callbacks


@pytest.mark.parametrize("group", ["clinical", "healthcare"])
def test_reviewed_references_have_no_reference_confirmation_action(group):
    _, keyboard = runtime.render_editor_review_materials(
        {
            "page": 1,
            "pageSize": 10,
            "totalItems": 1,
            "selectedGroup": group,
            "referenceReviewableCount": 0,
            "progress": {
                "normativeVersions": 0,
                "approvedNormativeVersions": 0,
                "referenceMaterials": 1,
                "reviewedReferenceMaterials": 1,
                "missingParts": 0,
                "unpreparedOriginals": 0,
                "unlinkedVersions": 0,
                "complete": True,
            },
            "items": [
                {
                    "materialId": str(UUID(int=23)),
                    "preparationId": str(UUID(int=23)),
                    "kind": "CLINICAL_REFERENCE",
                    "title": "Synthetic reviewed reference",
                    "referenceReviewed": True,
                }
            ],
        }
    )
    callbacks = [button.callback_data or "" for row in keyboard.inline_keyboard for button in row]
    assert not any(callback.startswith("editor:refbatch:") for callback in callbacks)
    assert f"editor:preparation:{UUID(int=23)}" in callbacks


@pytest.mark.parametrize(
    "visible_review_state",
    ["REVIEW_REQUIRED", "APPROVED"],
    ids=["pending-on-page", "pending-off-page"],
)
def test_pending_newer_normative_version_keeps_group_approval_action(visible_review_state):
    text, keyboard = runtime.render_editor_review_materials(
        {
            "page": 1,
            "pageSize": 1,
            "totalItems": 9,
            "selectedGroup": "healthcare",
            "referenceReviewableCount": 0,
            "progress": {
                "normativeVersions": 9,
                "approvedNormativeVersions": 8,
                "referenceMaterials": 0,
                "reviewedReferenceMaterials": 0,
                "missingParts": 0,
                "unpreparedOriginals": 0,
                "unlinkedVersions": 0,
                "complete": False,
            },
            "items": [
                {
                    "versionId": str(UUID(int=23)),
                    "title": "Synthetic norm version",
                    "reviewState": visible_review_state,
                }
            ],
        }
    )
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "Группа ещё не завершена." in text
    assert callbacks.count("editor:batch:healthcare") == 1
    assert "editor:group:healthcare:2" in callbacks


def test_pending_references_do_not_reopen_approval_of_all_approved_norms():
    _, keyboard = runtime.render_editor_review_materials(
        {
            "page": 1,
            "pageSize": 10,
            "totalItems": 11,
            "selectedGroup": "healthcare",
            "referenceReviewableCount": 1,
            "progress": {
                "normativeVersions": 9,
                "approvedNormativeVersions": 9,
                "referenceMaterials": 2,
                "reviewedReferenceMaterials": 1,
                "missingParts": 0,
                "unpreparedOriginals": 0,
                "unlinkedVersions": 0,
                "complete": False,
            },
            "items": [
                {
                    "materialId": str(UUID(int=23)),
                    "preparationId": str(UUID(int=23)),
                    "title": "Synthetic pending reference",
                    "kind": "CLINICAL_REFERENCE",
                    "referenceReviewed": False,
                }
            ],
        }
    )
    callbacks = [button.callback_data or "" for row in keyboard.inline_keyboard for button in row]
    assert "editor:refbatch:healthcare" in callbacks
    assert not any(callback.startswith("editor:batch:") for callback in callbacks)


@pytest.mark.parametrize("reviewed", [False, True], ids=["pending-reference", "reviewed-reference"])
def test_clinical_group_never_has_normative_approval_action(reviewed):
    _, keyboard = runtime.render_editor_review_materials(
        {
            "page": 1,
            "pageSize": 10,
            "totalItems": 1,
            "selectedGroup": "clinical",
            "referenceReviewableCount": int(not reviewed),
            "progress": {
                "normativeVersions": 0,
                "approvedNormativeVersions": 0,
                "referenceMaterials": 1,
                "reviewedReferenceMaterials": int(reviewed),
                "missingParts": 0,
                "unpreparedOriginals": 0,
                "unlinkedVersions": 0,
                "complete": reviewed,
            },
            "items": [
                {
                    "materialId": str(UUID(int=23)),
                    "preparationId": str(UUID(int=23)),
                    "title": "Synthetic clinical reference",
                    "kind": "CLINICAL_REFERENCE",
                    "referenceReviewed": reviewed,
                }
            ],
        }
    )
    callbacks = [button.callback_data or "" for row in keyboard.inline_keyboard for button in row]
    assert not any(callback.startswith("editor:batch:") for callback in callbacks)
    assert ("editor:refbatch:clinical" in callbacks) is (not reviewed)
