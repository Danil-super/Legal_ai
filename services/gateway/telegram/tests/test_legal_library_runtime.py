import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx2
import pytest
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler
from telegram_gateway import legal_library_runtime as runtime
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.legal_library_runtime import (
    _new_editor_state,
    build_application_with_legal_library,
    render_editor_review_materials,
    render_editor_version_detail,
    render_legal_library,
    render_platform_review_queue,
)


def _payload() -> dict[str, object]:
    title = "Правила предоставления платных медицинских услуг"
    return {
        "asOfDate": "2026-09-04",
        "items": [
            {
                "documentId": "00000000-0000-0000-0000-000000000001",
                "versionId": "00000000-0000-0000-0000-000000000002",
                "documentTitle": title,
                "issuer": "Правительство Российской Федерации",
                "officialNumber": "736",
                "effectiveFrom": "2023-09-01",
                "effectiveTo": "2026-09-01",
                "sourceUrl": "https://example.test/official.pdf",
                "rawSha256": "a" * 64,
                "fragmentCount": 7,
                "fragmentText": "This must never be rendered.",
            }
        ],
    }


def test_legal_library_renders_approved_metadata_and_official_link_only() -> None:
    text, keyboard = render_legal_library(_payload())

    assert "Правила предоставления" in text
    assert "Действует: 2023-09-01 — 2026-09-01" in text
    assert "aaaaaaaaaaaa…" in text
    assert "This must never be rendered." not in text
    assert keyboard.inline_keyboard[0][0].url == "https://example.test/official.pdf"
    assert keyboard.inline_keyboard[-1][0].callback_data == "menu"


def test_empty_legal_library_explains_that_legal_conclusions_stay_blocked() -> None:
    text, keyboard = render_legal_library({"asOfDate": "2026-09-04", "items": []})

    assert "нет одобренных источников" in text
    assert "заблокированными" in text
    assert keyboard.inline_keyboard[0][0].callback_data == "menu"


def test_platform_review_queue_renders_statuses_without_legal_text() -> None:
    text, keyboard = render_platform_review_queue(
        {
            "items": [
                {
                    "versionId": "00000000-0000-0000-0000-000000000002",
                    "documentTitle": "Правила платных медицинских услуг",
                    "officialNumber": "659",
                    "approvalState": "REVIEW_REQUIRED",
                    "artifactKind": "OFFICIAL_RAW",
                    "approvalEligible": True,
                    "effectiveFrom": "2026-09-01",
                    "effectiveTo": "2031-09-01",
                    "rawSha256": "a" * 64,
                    "fragmentCount": 11,
                    "normalizedText": "Raw text must stay hidden",
                }
            ]
        }
    )

    assert "ОЖИДАЕТ ПРОВЕРКИ" in text
    assert "Raw text must stay hidden" not in text
    assert keyboard.inline_keyboard[-1][0].callback_data == "menu"


def test_editor_detail_requires_all_four_explicit_attestations_before_confirm() -> None:
    detail = {
        "versionId": "00000000-0000-0000-0000-000000000002",
        "documentTitle": "Правила платных медицинских услуг",
        "issuer": "Правительство Российской Федерации",
        "officialNumber": "659",
        "sourceUrl": "https://example.test/official.pdf",
        "approvalState": "REVIEW_REQUIRED",
        "rawMimeType": "application/pdf",
        "rawSizeBytes": 100,
        "artifactPageCount": 1,
        "artifactRetrievedAt": "2026-09-09T12:00:00Z",
        "effectiveFrom": "2026-09-01",
        "effectiveTo": "2031-09-01",
        "rawSha256": "a" * 64,
        "normalizedSha256": "b" * 64,
        "fragmentsSha256": "c" * 64,
        "fragmentCount": 1,
        "approvalEligible": True,
    }
    state = _new_editor_state(detail)

    _, pending_keyboard = render_editor_version_detail(detail, state)
    pending_callbacks = {
        button.callback_data
        for row in pending_keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }
    assert "editor:confirm:00000000-0000-0000-0000-000000000002" not in pending_callbacks

    state["attestations"] = dict.fromkeys(("source", "artifact", "dates", "fragments"), True)
    _, approved_keyboard = render_editor_version_detail(detail, state)
    approved_callbacks = {
        button.callback_data
        for row in approved_keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }
    assert "editor:confirm:00000000-0000-0000-0000-000000000002" in approved_callbacks


def test_editor_detail_opens_the_preserved_pdf_instead_of_fragment_screen() -> None:
    detail = {
        "versionId": "00000000-0000-0000-0000-000000000002",
        "documentTitle": "Правила платных медицинских услуг",
        "issuer": "Правительство Российской Федерации",
        "officialNumber": "659",
        "sourceUrl": "https://example.test/official.pdf",
        "approvalState": "REVIEW_REQUIRED",
        "rawMimeType": "application/pdf",
        "rawSizeBytes": 100,
        "artifactPageCount": 1,
        "artifactRetrievedAt": "2026-09-09T12:00:00Z",
        "effectiveFrom": "2026-09-01",
        "effectiveTo": "2031-09-01",
        "rawSha256": "a" * 64,
        "normalizedSha256": "b" * 64,
        "fragmentsSha256": "c" * 64,
        "fragmentCount": 1,
        "approvalEligible": True,
    }

    _, keyboard = render_editor_version_detail(detail, _new_editor_state(detail))
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }

    assert "📄 Открыть PDF" in labels
    assert "📑 Проверить фрагменты" not in labels
    assert "editor:artifact:00000000-0000-0000-0000-000000000002" in callbacks
    assert not any(callback.startswith("editor:fragments:") for callback in callbacks)
    assert "📑 Полные выдержки для проверки" in labels
    assert "editor:excerpts:00000000-0000-0000-0000-000000000002" in callbacks


def test_editor_detail_displays_numeric_counts() -> None:
    detail = {
        "versionId": "00000000-0000-0000-0000-000000000002",
        "rawSizeBytes": 13579,
        "artifactPageCount": 42,
        "fragmentCount": 17,
    }
    rendered, _ = render_editor_version_detail(detail, _new_editor_state(detail))
    assert "13579 байт" in rendered
    assert "Страниц: 42" in rendered
    assert "Выбранных фрагментов: 17" in rendered


def test_editor_detail_labels_a_consultant_copy_without_calling_it_an_official_source() -> None:
    detail = {
        "versionId": "00000000-0000-0000-0000-000000000002",
        "documentTitle": "Федеральный закон",
        "issuer": "Российская Федерация",
        "officialNumber": "323-ФЗ",
        "sourceUrl": "https://www.consultant.ru/document/cons_doc_LAW_121895/",
        "approvalState": "REVIEW_REQUIRED",
        "artifactKind": "THIRD_PARTY_VERIFIED_COPY",
        "rawMimeType": "application/pdf",
        "rawSizeBytes": 100,
        "artifactPageCount": 1,
        "artifactRetrievedAt": "2026-09-11T12:00:00Z",
        "effectiveFrom": "2026-08-04",
        "effectiveTo": None,
        "rawSha256": "a" * 64,
        "normalizedSha256": "b" * 64,
        "fragmentsSha256": "c" * 64,
        "fragmentCount": 1,
        "approvalEligible": True,
    }

    text, keyboard = render_editor_version_detail(detail, _new_editor_state(detail))

    assert "КонсультантПлюс" in text
    assert "не первичная публикация" in text
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "🌐 Источник: КонсультантПлюс" in labels
    assert any("редакцией" in label for label in labels)


def test_editor_detail_labels_a_garant_rtf_copy_and_offers_the_document() -> None:
    detail = {
        "versionId": "00000000-0000-0000-0000-000000000002",
        "documentTitle": "Федеральный закон",
        "issuer": "Российская Федерация",
        "officialNumber": "323-ФЗ",
        "sourceUrl": "https://internet.garant.ru/document/redirect/12191967/0",
        "approvalState": "REVIEW_REQUIRED",
        "artifactKind": "THIRD_PARTY_VERIFIED_COPY",
        "rawMimeType": "application/rtf",
        "rawSizeBytes": 100,
        "artifactPageCount": None,
        "artifactRetrievedAt": "2026-09-25T12:00:00Z",
        "effectiveFrom": "2011-11-21",
        "effectiveTo": None,
        "rawSha256": "a" * 64,
        "normalizedSha256": "b" * 64,
        "fragmentsSha256": "c" * 64,
        "fragmentCount": 1,
        "approvalEligible": True,
    }

    text, keyboard = render_editor_version_detail(detail, _new_editor_state(detail))

    assert "Гарант" in text
    assert "не первичная публикация" in text
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "🌐 Источник: Гарант" in labels
    assert "📄 Открыть документ" in labels


def test_composed_application_registers_lawyer_library_before_menu_handler(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("AGENT_INTERNAL_KEY", raising=False)
    application = build_application_with_legal_library("123456:unit_test_token_value_1234567890")
    handlers = application.handlers.get(-4, [])

    assert any(
        isinstance(handler, CallbackQueryHandler)
        and getattr(handler, "pattern", None) is not None
        and "legalbase" in str(handler.pattern.pattern)
        for handler in handlers
    )
    assert any(isinstance(handler, CommandHandler) for handler in handlers)


@pytest.mark.parametrize("excerpts", [False, True])
def test_editor_attachment_is_complete_and_has_return_buttons(monkeypatch, excerpts) -> None:
    version_id = UUID("00000000-0000-0000-0000-000000000002")
    content = "Полная выдержка".encode() if excerpts else b"%PDF-1.7"
    mime = "text/plain" if excerpts else "application/pdf"
    client = SimpleNamespace(
        download_editor_artifact=AsyncMock(return_value=(content, mime)),
        download_editor_excerpts=AsyncMock(return_value=(content, mime)),
        aclose=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "LegalLibraryClient", lambda: client)
    monkeypatch.setattr(runtime.gateway_bot, "_actor_id", lambda _: 12345)
    message = SimpleNamespace(reply_document=AsyncMock())
    asyncio.run(
        runtime._send_editor_artifact(
            SimpleNamespace(effective_message=message), version_id=version_id, excerpts=excerpts
        )
    )
    sent = message.reply_document.call_args.kwargs
    assert sent["document"].input_file_content == content
    assert (
        sent["reply_markup"].inline_keyboard[0][0].callback_data == f"editor:detail:{version_id}:1"
    )
    assert sent["reply_markup"].inline_keyboard[1][0].callback_data == "editor:open"


def test_excerpts_client_checks_integrity_and_editor_credentials(monkeypatch) -> None:
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", "editor-test-gateway-key-12345678901234")
    version_id = UUID("00000000-0000-0000-0000-000000000002")
    content = "Полный текст выдержки".encode()
    tamper = False

    def serve(request):
        assert request.url.path == f"/v1/legal/review-queue/{version_id}/excerpts"
        assert request.headers["X-Telegram-User-Id"] == "12345"
        assert request.headers["X-Legal-Editor-Gateway-Key"]
        return httpx2.Response(
            200,
            content=content,
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "X-Legal-Artifact-Sha256": "f" * 64
                if tamper
                else hashlib.sha256(content).hexdigest(),
            },
        )

    async def scenario():
        nonlocal tamper
        async with httpx2.AsyncClient(
            base_url="http://legal-core:8000", transport=httpx2.MockTransport(serve)
        ) as http:
            client = runtime.LegalLibraryClient(client=http)
            assert await client.download_editor_excerpts(12345, version_id) == (
                content,
                "text/plain",
            )
            tamper = True
            with pytest.raises(LegalCoreApiError, match="Invalid artifact"):
                await client.download_editor_excerpts(12345, version_id)

    asyncio.run(scenario())


def test_editor_material_client_requires_the_paginated_review_contract(monkeypatch) -> None:
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", "editor-test-gateway-key-12345678901234")

    def serve(request):
        assert request.url.path == "/v1/legal/review-materials"
        assert request.url.query == b"page=1"
        return httpx2.Response(
            200,
            json={"page": 1, "pageSize": 10, "totalItems": 0, "items": []},
        )

    async def scenario() -> None:
        client = runtime.LegalLibraryClient(
            client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(serve), base_url="http://legal-core.test"
            )
        )
        try:
            assert await client.get_review_materials(12345, page=1) == {
                "page": 1,
                "pageSize": 10,
                "totalItems": 0,
                "items": [],
            }
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_editor_review_materials_are_openable_but_not_mislabeled_as_approved() -> None:
    material_id = "00000000-0000-0000-0000-000000000003"
    text, keyboard = render_editor_review_materials(
        {
            "page": 1,
            "pageSize": 10,
            "totalItems": 1,
            "selectedGroup": "healthcare",
            "items": [
                {
                    "materialId": material_id,
                    "title": "Федеральный закон № 323-ФЗ",
                    "kind": "LEGAL_COPY",
                    "reviewState": "METADATA_REQUIRED",
                    "sourceName": "Гарант",
                    "rawSha256": "a" * 64,
                }
            ],
        }
    )

    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }
    assert "не одобрены" in text
    assert "требуются реквизиты и проверка" in text
    assert f"editor:material:{material_id}" in callbacks


def test_material_groups_keep_counts_filter_pagination_and_back_navigation() -> None:
    payload = {
        "page": 1,
        "pageSize": 10,
        "totalItems": 21,
        "items": [],
        "selectedGroup": "labour",
        "groups": [
            {"key": "labour", "title": "Труд и квалификация", "totalItems": 21},
            {"key": "clinical", "title": "Клинические материалы", "totalItems": 7},
        ],
    }
    text, keyboard = render_editor_review_materials(payload)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "21" in text
    assert "editor:group:labour:2" in callbacks
    assert "editor:materials:1" in callbacks
    assert "editor:group:clinical:1" not in callbacks


def test_editor_directory_contains_only_seven_groups_and_back() -> None:
    keys = ("clinical", "labour", "courts", "privacy", "licensing", "healthcare", "general")
    payload = {
        "page": 1,
        "pageSize": 10,
        "totalItems": 64,
        "selectedGroup": None,
        "groups": [{"key": key, "title": key, "totalItems": 8} for key in keys],
        "items": [
            {
                "materialId": "00000000-0000-0000-0000-000000000003",
                "title": "This file must not appear at group level",
                "rawSha256": "a" * 64,
            }
        ],
    }
    text, keyboard = render_editor_review_materials(payload)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == [f"editor:group:{key}:1" for key in keys] + ["menu"]
    assert "This file must not appear" not in text
    assert "SHA" not in text
    assert "Страница" not in text


def test_group_displays_prepared_version_alongside_unprepared_material() -> None:
    version_id = "00000000-0000-0000-0000-000000000002"
    text, keyboard = render_editor_review_materials(
        {
            "page": 1,
            "pageSize": 10,
            "totalItems": 1,
            "selectedGroup": "healthcare",
            "groups": [{"key": "healthcare", "title": "Медицинские документы", "totalItems": 1}],
            "items": [
                {
                    "materialId": None,
                    "versionId": version_id,
                    "title": "Подготовленный акт",
                    "reviewState": "REVIEW_REQUIRED",
                }
            ],
        }
    )
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "Подготовленный акт" not in text
    assert "Медицинские документы" not in text
    assert f"editor:detail:{version_id}:1" in callbacks
    assert "editor:materials:1" in callbacks


def test_group_preview_lists_exact_ready_subset_and_explicit_declaration() -> None:
    batch = "00000000-0000-0000-0000-000000000004"
    text, keyboard = runtime.render_group_approval_preview(
        {
            "group": "healthcare",
            "snapshot": "a" * 64,
            "alreadyApproved": 0,
            "ready": [
                {
                    "versionId": "00000000-0000-0000-0000-000000000002",
                    "title": "Готовый документ",
                    "effectiveFrom": "2026-01-01",
                    "effectiveTo": None,
                }
            ],
            "blocked": [{"title": "Неподготовленный документ", "reasonCode": "METADATA_REQUIRED"}],
        },
        batch_id=batch,
        page=1,
    )
    assert "Готовый документ" in text
    assert "2026-01-01" in text
    assert "Неподготовленный документ" in text
    assert "Подтверждаю" in text
    assert "источник" in text and "фрагменты" in text and "даты" in text
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert f"editor:batchconfirm:{batch}" in callbacks


def test_group_preview_never_offers_approval_for_only_unprepared_files() -> None:
    text, keyboard = runtime.render_group_approval_preview(
        {
            "group": "clinical",
            "snapshot": "a" * 64,
            "alreadyApproved": 0,
            "ready": [],
            "blocked": [
                {
                    "title": "Клинический материал",
                    "reasonCode": "CLINICAL_REFERENCE_NOT_LEGAL_VERSION",
                }
            ],
        },
        batch_id="00000000-0000-0000-0000-000000000004",
        page=1,
    )
    assert "Клинический материал" in text
    assert not any(
        button.callback_data.startswith("editor:batchconfirm:")
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_group_confirmation_requires_matching_preview_and_preserves_retry_key(monkeypatch):
    preview = {
        "group": "general",
        "snapshot": "a" * 64,
        "blocked": [],
        "ready": [
            {
                "versionId": "00000000-0000-0000-0000-000000000002",
                "title": "Тестовый акт",
                "effectiveFrom": "2026-01-01",
                "effectiveTo": None,
            }
        ],
    }
    client = SimpleNamespace(
        get_group_preview=AsyncMock(return_value=preview),
        aclose=AsyncMock(),
        approve_group=AsyncMock(
            side_effect=[
                LegalCoreApiError(503, "TIMEOUT", "unknown outcome"),
                {"approvedCount": 1},
            ]
        ),
    )
    monkeypatch.setattr(runtime, "LegalLibraryClient", lambda: client)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(user_data={})

    async def scenario():
        await runtime._show_group_approval(update, context, group="general")
        pending = context.user_data[runtime._EDITOR_GROUP_PENDING_KEY]
        key = pending["id"]
        await runtime._confirm_group_approval(update, context, batch_id=str(UUID(int=1)))
        client.approve_group.assert_not_awaited()
        await runtime._confirm_group_approval(update, context, batch_id=key)
        assert context.user_data[runtime._EDITOR_GROUP_PENDING_KEY]["id"] == key
        await runtime._confirm_group_approval(update, context, batch_id=key)
        assert runtime._EDITOR_GROUP_PENDING_KEY not in context.user_data
        first, second = client.approve_group.await_args_list
        assert first == second
        assert first.args[-1] == UUID(key)
        assert first.args[2]["fragmentsVerified"] is True

    asyncio.run(scenario())


def test_group_preview_reports_previously_approved_without_offering_confirmation():
    text, keyboard = runtime.render_group_approval_preview(
        {"group": "general", "ready": [], "blocked": [], "alreadyApproved": 3},
        batch_id=str(UUID(int=1)),
        page=1,
    )
    assert "Ранее утверждено: 3" in text
    assert not any(
        button.callback_data.startswith("editor:batchconfirm:")
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_group_preview_fits_telegram_utf16_limit_without_losing_declaration():
    text, keyboard = runtime.render_group_approval_preview(
        {
            "group": "general",
            "alreadyApproved": 200,
            "ready": [
                {"title": "😀" * 2000, "effectiveFrom": "2026-01-01", "effectiveTo": "2030-01-01"}
                for _ in range(10)
            ],
            "blocked": [
                {"title": "😀" * 2000, "reasonCode": "METADATA_REQUIRED"} for _ in range(200)
            ],
        },
        batch_id=str(UUID(int=1)),
        page=1,
    )
    assert len(text.encode("utf-16-le")) // 2 <= 4096
    assert "Подтверждаю проверку всех перечисленных документов" in text
    assert all(
        len(button.callback_data.encode()) <= 64
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_editor_review_material_client_checks_integrity_and_editor_credentials(monkeypatch) -> None:
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", "editor-test-gateway-key-12345678901234")
    material_id = UUID("00000000-0000-0000-0000-000000000003")
    content = b"{\\rtf1\\ansi review material}"

    def serve(request):
        assert request.url.path == f"/v1/legal/review-materials/{material_id}/artifact"
        assert request.headers["X-Telegram-User-Id"] == "12345"
        assert request.headers["X-Legal-Editor-Gateway-Key"]
        return httpx2.Response(
            200,
            content=content,
            headers={
                "Content-Type": "application/rtf",
                "X-Legal-Artifact-Sha256": hashlib.sha256(content).hexdigest(),
            },
        )

    async def scenario() -> None:
        async with httpx2.AsyncClient(
            base_url="http://legal-core:8000", transport=httpx2.MockTransport(serve)
        ) as http:
            client = runtime.LegalLibraryClient(client=http)
            assert await client.download_editor_review_material(12345, material_id) == (
                content,
                "application/rtf",
            )

    asyncio.run(scenario())


def test_return_from_pdf_keeps_attestations_for_the_same_immutable_version(monkeypatch) -> None:
    version_id = "00000000-0000-0000-0000-000000000002"
    detail = {"versionId": version_id, "rawSha256": "a" * 64}
    pending = _new_editor_state(detail)
    pending["attestations"]["source"] = True
    context = SimpleNamespace(user_data={runtime._EDITOR_PENDING_KEY: pending})
    monkeypatch.setattr(runtime.gateway_bot, "_actor_id", lambda _: 12345)
    monkeypatch.setattr(
        runtime.gateway_bot,
        "_answer_callback",
        AsyncMock(return_value=f"editor:detail:{version_id}:1"),
    )
    monkeypatch.setattr(runtime, "_editor_reply", AsyncMock())
    monkeypatch.setattr(
        runtime,
        "LegalLibraryClient",
        lambda: SimpleNamespace(
            get_editor_version=AsyncMock(return_value=detail), aclose=AsyncMock()
        ),
    )
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(runtime.legal_editor_callback(SimpleNamespace(), context))
    assert context.user_data[runtime._EDITOR_PENDING_KEY]["attestations"]["source"] is True


def test_queue_does_not_mislabel_deferred_integrity_checks_as_unavailable() -> None:
    rendered, _ = render_platform_review_queue(
        {
            "items": [
                {
                    "versionId": "00000000-0000-0000-0000-000000000002",
                    "approvalState": "REVIEW_REQUIRED",
                    "approvalEligible": False,
                    "approvalPreflightChecked": False,
                }
            ]
        }
    )
    assert "проверка доступности — при открытии карточки" in rendered
    assert "старая/недоступная" not in rendered


@pytest.mark.parametrize("resource", ["artifact", "excerpts"])
def test_editor_file_callback_returns_before_download_completes(monkeypatch, resource) -> None:
    version_id = "00000000-0000-0000-0000-000000000002"
    monkeypatch.setattr(runtime.gateway_bot, "_actor_id", lambda _: 12345)
    monkeypatch.setattr(
        runtime.gateway_bot,
        "_answer_callback",
        AsyncMock(return_value=f"editor:{resource}:{version_id}"),
    )
    monkeypatch.setattr(runtime.gateway_bot, "_reply", AsyncMock())

    async def scenario():
        finished = asyncio.Event()
        started = asyncio.Event()

        async def send(*args, **kwargs):
            started.set()
            await finished.wait()

        monkeypatch.setattr(runtime, "_send_editor_artifact", send)
        application = SimpleNamespace(running=True, bot_data={}, create_task=asyncio.create_task)
        context = SimpleNamespace(application=application, user_data={"unrelated": "keep"})
        with pytest.raises(ApplicationHandlerStop):
            await asyncio.wait_for(
                runtime.legal_editor_callback(SimpleNamespace(effective_message=object()), context),
                timeout=0.1,
            )
        await started.wait()
        assert not finished.is_set()
        assert context.user_data == {"unrelated": "keep"}
        finished.set()
        await application.bot_data[runtime._EDITOR_DELIVERY_KEY].drain()

    asyncio.run(scenario())


def test_clinical_group_has_reference_confirmation_without_repeating_titles() -> None:
    material_id = "00000000-0000-0000-0000-000000000003"
    text, keyboard = render_editor_review_materials(
        {
            "page": 1,
            "pageSize": 10,
            "totalItems": 1,
            "referenceReviewableCount": 1,
            "selectedGroup": "clinical",
            "groups": [
                {
                    "key": "clinical",
                    "title": "Клинические справочные материалы",
                    "totalItems": 1,
                }
            ],
            "items": [
                {
                    "materialId": material_id,
                    "versionId": None,
                    "title": "Кариес зубов",
                    "kind": "CLINICAL_REFERENCE",
                    "reviewState": "METADATA_REQUIRED",
                    "preparationId": material_id,
                }
            ],
        }
    )
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    callbacks = [button.callback_data for button in buttons]
    assert "Клинические справочные материалы" not in text
    assert "Кариес зубов" not in text
    assert "editor:refbatch:clinical" in callbacks
    assert "editor:batch:clinical" not in callbacks
    assert any(button.text == "📄 Открыть: Кариес зубов" for button in buttons)


def test_mixed_group_shows_distinct_norm_and_reference_actions() -> None:
    _, keyboard = render_editor_review_materials(
        {
            "page": 1,
            "pageSize": 10,
            "totalItems": 2,
            "referenceReviewableCount": 1,
            "selectedGroup": "healthcare",
            "groups": [{"key": "healthcare", "title": "Медицинская деятельность", "totalItems": 2}],
            "items": [
                {
                    "materialId": "00000000-0000-0000-0000-000000000003",
                    "versionId": None,
                    "title": "Форма 043",
                    "kind": "LEGAL_COPY",
                    "reviewState": "METADATA_REQUIRED",
                    "preparationId": "00000000-0000-0000-0000-000000000003",
                }
            ],
        }
    )
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "editor:refbatch:healthcare" in callbacks
    assert "editor:batch:healthcare" in callbacks


def test_reference_preview_and_confirmation_reuse_one_retry_key(monkeypatch) -> None:
    preparation_id = "00000000-0000-0000-0000-000000000003"
    preview = {
        "group": "clinical",
        "snapshot": "a" * 64,
        "alreadyReviewed": 0,
        "ready": [
            {
                "preparationId": preparation_id,
                "title": "Кариес зубов",
                "kind": "CLINICAL_REFERENCE",
            }
        ],
    }
    client = SimpleNamespace(
        get_reference_review_preview=AsyncMock(return_value=preview),
        confirm_reference_review=AsyncMock(
            side_effect=[
                LegalCoreApiError(503, "TIMEOUT", "unknown outcome"),
                {"reviewedCount": 1},
            ]
        ),
        aclose=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "LegalLibraryClient", lambda: client)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(user_data={})

    async def scenario() -> None:
        await runtime._show_reference_review(update, context, group="clinical")
        pending = context.user_data[runtime._EDITOR_REFERENCE_PENDING_KEY]
        key = pending["id"]
        await runtime._confirm_reference_review(update, context, batch_id=key)
        assert context.user_data[runtime._EDITOR_REFERENCE_PENDING_KEY]["id"] == key
        await runtime._confirm_reference_review(update, context, batch_id=key)
        assert runtime._EDITOR_REFERENCE_PENDING_KEY not in context.user_data
        first, second = client.confirm_reference_review.await_args_list
        assert first == second
        assert first.args[-1] == UUID(key)
        assert first.args[2]["referenceOnlyUnderstood"] is True

    asyncio.run(scenario())
