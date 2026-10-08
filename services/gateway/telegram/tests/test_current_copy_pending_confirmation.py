"""A hidden approval button must also disable callbacks in server-owned pending state."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from telegram_gateway import legal_library_runtime as runtime

VERSION_ID = UUID(int=75)
_MISSING = "missing"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    fake = SimpleNamespace(
        get_group_preview=AsyncMock(),
        approve_group=AsyncMock(return_value={"approvedCount": 1}),
        approve_editor_version=AsyncMock(return_value={"approvedAt": "2026-10-08T00:00:00Z"}),
        aclose=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "LegalLibraryClient", lambda: fake)
    monkeypatch.setattr(runtime.gateway_bot, "_actor_id", lambda update: 1)
    monkeypatch.setattr(runtime.gateway_bot, "_reply", AsyncMock())
    monkeypatch.setattr(runtime, "_editor_reply", AsyncMock())
    return fake


def _detail(*, oversized: bool = False) -> dict:
    return {
        "versionId": str(VERSION_ID),
        "documentTitle": "Synthetic current copy",
        "issuer": None,
        "officialNumber": None,
        "sourceUrl": "https://internet.garant.ru/document/redirect/75/0",
        "approvalState": "REVIEW_REQUIRED",
        "approvalEligible": True,
        "artifactKind": "THIRD_PARTY_VERIFIED_COPY",
        "rawMimeType": "application/rtf",
        "rawSizeBytes": 100,
        "artifactRetrievedAt": "2026-10-08T00:00:00Z",
        "artifactPageCount": None,
        "effectiveFrom": "2026-10-08",
        "effectiveTo": None,
        "rawSha256": "a" * 64,
        "normalizedSha256": "b" * 64,
        "fragmentsSha256": "c" * 64,
        "fragmentCount": 1,
        "dateBasis": "LAWYER_CURRENT_COPY",
        "extractionLimitations": (
            [f"SYNTHETIC_LIMIT_{i}: " + "sample " * 130 for i in range(10)]
            if oversized
            else ["SYNTHETIC_LIMIT: graphic-dependent fragments excluded."]
        ),
    }


def _preview(*, oversized: bool = False, count: int = 1) -> dict:
    return {
        "group": "general",
        "snapshot": "a" * 64,
        "alreadyApproved": 0,
        "blocked": [],
        "ready": [
            {
                "versionId": str(UUID(int=index + 1)),
                "title": f"Synthetic document {index + 1}",
                "effectiveFrom": "2026-10-08",
                "effectiveTo": None,
                "dateBasis": "LAWYER_CURRENT_COPY",
                "extractionLimitations": (
                    [f"SYNTHETIC_LIMIT_{index}: " + "sample " * 130]
                    if oversized
                    else ["SYNTHETIC_LIMIT: graphic-dependent fragments excluded."]
                ),
            }
            for index in range(count)
        ],
    }


def _set_enabled(pending: dict, flag: object) -> None:
    if flag == _MISSING:
        pending.pop("confirmationEnabled", None)
    else:
        pending["confirmationEnabled"] = flag


@pytest.mark.parametrize("oversized", [False, True], ids=["full-render", "overflow"])
def test_single_pending_enablement_requires_the_complete_prospective_checklist(
    oversized: bool,
) -> None:
    pending = runtime._new_editor_state(_detail(oversized=oversized))

    assert pending.get("confirmationEnabled") is (not oversized)
    assert all(value is False for value in pending["attestations"].values())


@pytest.mark.parametrize("flag", [_MISSING, False, None, 0, 1, "true"])
def test_single_callback_rejects_missing_false_or_non_boolean_enablement(
    client: SimpleNamespace, flag: object
) -> None:
    pending = runtime._new_editor_state(_detail())
    pending["attestations"] = dict.fromkeys(("source", "artifact", "dates", "fragments"), True)
    _set_enabled(pending, flag)
    context = SimpleNamespace(user_data={runtime._EDITOR_PENDING_KEY: pending})

    asyncio.run(runtime._confirm_editor_approval(SimpleNamespace(), context, version_id=VERSION_ID))

    client.approve_editor_version.assert_not_awaited()


def test_single_callback_accepts_a_fully_shown_and_explicitly_attested_checklist(
    client: SimpleNamespace,
) -> None:
    pending = runtime._new_editor_state(_detail())
    assert pending.get("confirmationEnabled") is True
    pending["attestations"] = dict.fromkeys(("source", "artifact", "dates", "fragments"), True)
    context = SimpleNamespace(user_data={runtime._EDITOR_PENDING_KEY: pending})

    asyncio.run(runtime._confirm_editor_approval(SimpleNamespace(), context, version_id=VERSION_ID))

    client.approve_editor_version.assert_awaited_once()


@pytest.mark.parametrize("flag", [_MISSING, False, None, 0, 1, "true"])
def test_group_callback_rejects_missing_false_or_non_boolean_enablement(
    client: SimpleNamespace, flag: object
) -> None:
    preview = _preview()
    pending = {
        "id": str(VERSION_ID),
        "group": "general",
        "preview": preview,
        "ids": [preview["ready"][0]["versionId"]],
        "lastPageShown": True,
    }
    _set_enabled(pending, flag)
    context = SimpleNamespace(user_data={runtime._EDITOR_GROUP_PENDING_KEY: pending})

    asyncio.run(
        runtime._confirm_group_approval(SimpleNamespace(), context, batch_id=str(VERSION_ID))
    )

    client.approve_group.assert_not_awaited()


@pytest.mark.parametrize("oversized", [False, True], ids=["full-render", "overflow"])
def test_group_pending_state_blocks_callbacks_when_the_actual_checklist_does_not_fit(
    client: SimpleNamespace, oversized: bool
) -> None:
    client.get_group_preview.return_value = _preview(oversized=oversized, count=10)
    context = SimpleNamespace(user_data={})

    async def scenario() -> None:
        await runtime._show_group_approval(SimpleNamespace(), context, group="general")
        pending = context.user_data[runtime._EDITOR_GROUP_PENDING_KEY]
        assert pending.get("confirmationEnabled") is (not oversized)
        client.approve_group.assert_not_awaited()
        # Simulate a callback independently of whether a confirm button was rendered.
        pending["lastPageShown"] = True
        await runtime._confirm_group_approval(SimpleNamespace(), context, batch_id=pending["id"])

    asyncio.run(scenario())

    if oversized:
        client.approve_group.assert_not_awaited()
    else:
        client.approve_group.assert_awaited_once()


def test_group_enablement_checks_overflow_on_every_page_not_only_the_last(
    client: SimpleNamespace,
) -> None:
    preview = _preview(count=11)
    for item in preview["ready"]:
        item["title"] = "Synthetic document " + "x" * 80
        item["extractionLimitations"] = [
            f"SYNTHETIC_LIMIT_{index}: " + "sample " * 125 for index in range(3)
        ]
    client.get_group_preview.return_value = preview
    context = SimpleNamespace(user_data={})

    async def scenario() -> None:
        await runtime._show_group_approval(SimpleNamespace(), context, group="general")
        pending = context.user_data[runtime._EDITOR_GROUP_PENDING_KEY]
        assert pending.get("confirmationEnabled") is False
        # Visiting the last page cannot repair an earlier undisplayed checklist.
        pending["lastPageShown"] = True
        await runtime._confirm_group_approval(SimpleNamespace(), context, batch_id=pending["id"])

    asyncio.run(scenario())

    client.approve_group.assert_not_awaited()
