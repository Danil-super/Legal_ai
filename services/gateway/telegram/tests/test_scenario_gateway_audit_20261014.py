"""Read-only audit reproductions. All inputs are fixed synthetic examples."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx2
import pytest
from legal_core.api_contracts import TelegramIntakeDraftUpdateRequest
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from telegram import Update
from telegram.ext import ExtBot
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError, LegalCoreClient
from test_case_experience import complete_data
from test_dialog_isolation import ACTOR, DRAFT
from test_guided_case_intake_v2 import _base_data
from test_unified_intake_scenarios import versioned_harness


async def dispatch_raw(app, *, content=None, media=None, message_id=999, update_id=1000):
    user = {"id": ACTOR, "is_bot": False, "first_name": "Synthetic"}
    message = {
        "message_id": message_id,
        "date": int(datetime.now(UTC).timestamp()),
        "from": user,
        "chat": {"id": ACTOR, "type": "private"},
    }
    payload = {"update_id": update_id}
    if media is not None:
        message.update(media)
        payload["message"] = message
    else:
        payload["callback_query"] = {
            "id": str(update_id),
            "chat_instance": "synthetic",
            "from": user,
            "message": message,
            "data": content,
        }
    await app.process_update(Update.de_json(payload, app.bot))


@pytest.mark.parametrize("filename", ["example.txt", "пример.txt"])
def test_case_material_filename_reaches_core_transport(filename):
    async def scenario():
        requests = []

        async def receive(request):
            requests.append(request)
            return httpx2.Response(201, json={"draftRevision": 2, "material": {"id": str(DRAFT)}})

        async with httpx2.AsyncClient(
            base_url="http://synthetic.invalid", transport=httpx2.MockTransport(receive)
        ) as http:
            client = LegalCoreClient(http)
            result = await client.upload_case_material(
                DRAFT,
                ACTOR,
                content=b"Fixed synthetic example.",
                source_filename=filename,
                content_type="text/plain",
            )
        assert len(requests) == 1
        assert result["draftRevision"] == 2

    asyncio.run(scenario())


def test_revoked_actor_does_not_receive_cached_draft_summary_before_denied_write():
    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            data = _base_data()
            data["safetyScreening"] = {
                "schemaVersion": SCREENING_VERSION,
                **dict.fromkeys(SCREENING_FIELDS[:-1], "NO"),
            }
            core.draft = {
                "id": str(DRAFT),
                "revision": 7,
                "wizardState": "SAFETY",
                "draftData": data,
            }
            await dispatch(f"case:draft:{DRAFT}")
            before = deepcopy(core.draft)
            count = len(sent)
            core.deny = True
            await dispatch("case:safety:moneyRequested:NO")
            assert core.draft == before
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            assert not any(data["eventSummary"] in item.get("text", "") for item in sent[count:]), (
                "The revoked actor was sent tenant content before Core rejected the save"
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("filename", ["example.txt", "пример.txt"])
def test_document_handler_uploads_supported_synthetic_file(filename):
    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            core.draft = {
                "id": str(DRAFT),
                "revision": 7,
                "wizardState": "MATERIALS",
                "draftData": _base_data(),
            }
            await dispatch(f"case:draft:{DRAFT}")
            calls = []

            async def receive(request):
                calls.append(request)
                core.draft["revision"] += 1
                core.draft["draftData"]["caseMaterialsStatus"] = "ATTACHED"
                return httpx2.Response(
                    201,
                    json={
                        "draftRevision": core.draft["revision"],
                        "material": {"id": str(UUID(int=555))},
                    },
                )

            async with httpx2.AsyncClient(
                base_url="http://synthetic.invalid", transport=httpx2.MockTransport(receive)
            ) as http:
                core.upload_case_material = LegalCoreClient(http).upload_case_material
                file = type(
                    "SyntheticFile",
                    (),
                    {
                        "download_as_bytearray": AsyncMock(
                            return_value=bytearray(b"Fixed synthetic material without identifiers.")
                        )
                    },
                )()
                with patch.object(ExtBot, "get_file", AsyncMock(return_value=file)):
                    await dispatch_raw(
                        app,
                        media={
                            "document": {
                                "file_id": "synthetic",
                                "file_unique_id": "synthetic",
                                "file_name": filename,
                                "mime_type": "text/plain",
                                "file_size": 52,
                            }
                        },
                    )
            assert len(calls) == 1, sent[-1]["text"]
            assert core.draft["draftData"]["caseMaterialsStatus"] == "ATTACHED"
            assert core.draft["draftData"]["incomingSourceStatus"] == "FILE_ATTACHED"
            assert any("материал добавлен" in item.get("text", "") for item in sent)

    asyncio.run(scenario())


def test_stale_incoming_button_from_previous_draft_does_not_answer_new_draft():
    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            await dispatch("case:start")
            first_prompt = len(sent) + 100
            await dispatch("/menu")
            second_id = UUID(int=124)

            async def create(actor, *, intake_schema_version):
                core.draft = {
                    "id": str(second_id),
                    "revision": 1,
                    "wizardState": "INCOMING",
                    "draftData": {"intakeVersion": 2},
                }
                return deepcopy(core.draft)

            async def save(draft, actor, **kwargs):
                assert draft == second_id
                core.draft = {
                    "id": str(draft),
                    "revision": kwargs["expected_revision"] + 1,
                    "wizardState": kwargs["wizard_state"],
                    "draftData": deepcopy(kwargs["draft_data"]),
                }
                core.saved.append(deepcopy(core.draft))
                return deepcopy(core.draft)

            core.create_intake_draft = create
            core.save_intake_draft = save
            await dispatch("case:start")
            before = deepcopy(core.draft)
            await dispatch_raw(
                app, content="case:v2:single:incomingKind:COMPLAINT", message_id=first_prompt
            )
            assert core.draft == before, "An old draft's button changed the new draft"

    asyncio.run(scenario())


def test_old_unchecked_multi_button_does_not_reverse_the_current_checked_selection():
    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            await dispatch("case:start")
            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            unchecked_message = len(sent) + 100
            callback = "case:v2:multi:situationAreas:TREATMENT"
            await dispatch_raw(app, content=callback, message_id=unchecked_message)
            assert core.draft["draftData"]["situationAreas"] == ["TREATMENT"]
            await dispatch_raw(app, content=callback, message_id=unchecked_message, update_id=1001)
            assert core.draft["draftData"]["situationAreas"] == ["TREATMENT"], (
                "Repeated click of an old unchecked button removed the selected answer"
            )

    asyncio.run(scenario())


def test_photo_at_material_step_receives_format_guidance_without_mutation():
    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            core.draft = {
                "id": str(DRAFT),
                "revision": 7,
                "wizardState": "MATERIALS",
                "draftData": _base_data(),
            }
            await dispatch(f"case:draft:{DRAFT}")
            before = deepcopy(core.draft)
            count = len(sent)
            await dispatch_raw(
                app,
                media={
                    "photo": [
                        {
                            "file_id": "synthetic-photo",
                            "file_unique_id": "synthetic",
                            "width": 10,
                            "height": 10,
                        }
                    ]
                },
            )
            assert core.draft == before
            assert len(sent) > count, "Unsupported media was silently ignored"

    asyncio.run(scenario())


def test_ambiguous_committed_attachment_does_not_break_next_material_action():
    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            core.draft = {
                "id": str(DRAFT),
                "revision": 7,
                "wizardState": "MATERIALS",
                "draftData": _base_data(),
            }
            await dispatch(f"case:draft:{DRAFT}")

            async def upload(*args, **kwargs):
                core.draft["revision"] += 1
                core.draft["draftData"]["caseMaterialsStatus"] = "ATTACHED"
                raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "synthetic lost response")

            core.upload_case_material = upload
            await dispatch("text:Fixed synthetic material without identifying information.")
            assert core.draft["draftData"]["caseMaterialsStatus"] == "ATTACHED"
            await dispatch("case:v2:material:skip")
            assert bot.WIZARD_DATA_KEY in app.user_data[ACTOR], (
                "Upload committed, but the stale gateway revision ended the conversation"
            )
            assert core.draft["draftData"]["caseMaterialsStatus"] == "ATTACHED"
            await dispatch("case:v2:material:done")
            assert core.draft["wizardState"] == "SAFETY"

    asyncio.run(scenario())


def test_old_skip_button_does_not_mark_existing_material_as_absent():
    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            core.draft = {
                "id": str(DRAFT),
                "revision": 7,
                "wizardState": "MATERIALS",
                "draftData": _base_data(),
            }
            await dispatch(f"case:draft:{DRAFT}")
            original_skip_message = len(sent) + 100

            async def upload(*args, **kwargs):
                core.draft["revision"] += 1
                core.draft["draftData"]["caseMaterialsStatus"] = "ATTACHED"
                return {
                    "draftRevision": core.draft["revision"],
                    "material": {"id": str(UUID(int=555))},
                }

            core.upload_case_material = upload
            await dispatch("text:Fixed synthetic material without identifying information.")
            assert core.draft["draftData"]["caseMaterialsStatus"] == "ATTACHED"
            assert core.draft["draftData"]["incomingSourceStatus"] == "TEXT_ATTACHED"
            await dispatch_raw(
                app, content="case:v2:material:skip", message_id=original_skip_message
            )
            assert core.draft["draftData"]["caseMaterialsStatus"] == "ATTACHED", (
                "The old skip button marked the stored attachment NOT_ATTACHED"
            )

    asyncio.run(scenario())


def test_text_material_upload_and_done_control_preserves_attached_status():
    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            core.draft = {
                "id": str(DRAFT),
                "revision": 7,
                "wizardState": "MATERIALS",
                "draftData": _base_data(),
            }
            await dispatch(f"case:draft:{DRAFT}")

            async def upload(*args, **kwargs):
                core.draft["revision"] += 1
                core.draft["draftData"]["caseMaterialsStatus"] = "ATTACHED"
                return {
                    "draftRevision": core.draft["revision"],
                    "material": {"id": str(UUID(int=555))},
                }

            core.upload_case_material = upload
            await dispatch("text:Fixed synthetic material without identifying information.")
            await dispatch("case:v2:material:done")
            assert core.draft["draftData"]["caseMaterialsStatus"] == "ATTACHED"
            assert core.draft["wizardState"] == "SAFETY"
            assert bot.WIZARD_DATA_KEY in app.user_data[ACTOR]

    asyncio.run(scenario())


@pytest.mark.parametrize("navigation", ["/start", "/cancel", "menu"])
def test_navigation_preserves_answers_and_resumes_the_same_v2_step(navigation):
    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            await dispatch("case:start")
            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            await dispatch("case:v2:multi:situationAreas:TREATMENT")
            await dispatch("case:v2:multi:situationAreas:DONE")
            assert core.draft["wizardState"] == "SERVICES"
            before = deepcopy(core.draft)
            await dispatch(navigation)
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            assert core.draft == before
            await dispatch(f"case:draft:{DRAFT}")
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SERVICES"
            assert core.draft == before

    asyncio.run(scenario())


def test_wrong_text_at_button_step_does_not_change_or_echo_the_draft():
    async def scenario():
        async with versioned_harness() as (_app, core, sent, dispatch):
            await dispatch("case:start")
            before = deepcopy(core.draft)
            raw = "Fixed unrelated message sent while the bot awaits a button."
            count = len(sent)
            await dispatch("text:" + raw)
            assert core.draft == before
            assert sent[count:]
            assert not any(raw in item.get("text", "") for item in sent[count:])

    asyncio.run(scenario())


def test_latest_checked_button_can_deselect_an_answer():
    async def scenario():
        async with versioned_harness() as (_app, core, _sent, dispatch):
            await dispatch("case:start")
            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            await dispatch("case:v2:multi:situationAreas:TREATMENT")
            assert core.draft["draftData"]["situationAreas"] == ["TREATMENT"]
            await dispatch("case:v2:multi:situationAreas:TREATMENT")
            assert core.draft["draftData"]["situationAreas"] == []

    asyncio.run(scenario())


def test_remote_revision_change_refreshes_selection_before_consuming_an_old_answer():
    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            await dispatch("case:start")
            core.draft.update(
                revision=2,
                wizardState="SITUATION",
                draftData={
                    "intakeVersion": 2,
                    "incomingKind": "MESSAGE_OR_REQUEST",
                    "incomingSourceStatus": "NOT_ATTACHED",
                },
            )
            before = deepcopy(core.draft)
            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            assert core.draft == before
            current = app.user_data[ACTOR][bot.WIZARD_DATA_KEY]
            assert current[bot.DRAFT_REVISION_KEY] == 2
            assert current[bot.DRAFT_STATE_KEY] == "SITUATION"
            assert current["incomingKind"] == "MESSAGE_OR_REQUEST"
            assert core.saved == []

    asyncio.run(scenario())


@pytest.mark.parametrize("lost_save", ["before_commit", "after_commit", "invalid_response"])
def test_confirmed_upload_receipt_survives_failed_secondary_draft_save(lost_save):
    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            core.draft = {
                "id": str(DRAFT),
                "revision": 7,
                "wizardState": "MATERIALS",
                "draftData": _base_data(),
            }
            await dispatch(f"case:draft:{DRAFT}")

            async def upload(*args, **kwargs):
                core.draft["revision"] += 1
                core.draft["draftData"]["caseMaterialsStatus"] = "ATTACHED"
                return {
                    "draftRevision": core.draft["revision"],
                    "material": {"id": str(UUID(int=555))},
                }

            original = core._json_request
            fail = True

            async def save(method, path, **kwargs):
                nonlocal fail
                if method == "PUT" and fail:
                    fail = False
                    if lost_save == "before_commit":
                        raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "synthetic timeout")
                    result = await original(method, path, **kwargs)
                    if lost_save == "invalid_response":
                        return result | {"revision": "invalid"}
                    raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "synthetic timeout")
                return await original(method, path, **kwargs)

            core.upload_case_material = upload
            core._json_request = save
            await dispatch("text:Fixed synthetic material without identifying information.")
            data = app.user_data[ACTOR][bot.WIZARD_DATA_KEY]
            assert data[bot.DRAFT_REVISION_KEY] == core.draft["revision"]
            assert data["caseMaterialsStatus"] == "ATTACHED"
            assert data["incomingSourceStatus"] == "TEXT_ATTACHED"
            assert any("Материал сохранён" in item.get("text", "") for item in sent)
            assert not any("Файл не добавлен" in item.get("text", "") for item in sent)
            await dispatch("case:v2:material:done")
            assert core.draft["wizardState"] == "SAFETY"
            assert core.draft["draftData"]["incomingSourceStatus"] == "TEXT_ATTACHED"

    asyncio.run(scenario())


def test_back_button_on_current_validation_message_is_usable():
    async def scenario():
        async with versioned_harness() as (_app, core, _sent, dispatch):
            data = _base_data()
            data.pop("eventDate")
            core.draft = {
                "id": str(DRAFT),
                "revision": 7,
                "wizardState": "EVENT_DATE",
                "draftData": data,
            }
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:not a valid date")
            assert core.draft["wizardState"] == "EVENT_DATE"
            await dispatch("case:v2:back")
            assert core.draft["wizardState"] == "EVENT"

    asyncio.run(scenario())


@pytest.mark.parametrize("old_button", [False, True])
def test_legacy_v1_same_state_buttons_apply_only_to_the_current_prompt(old_button):
    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            other = UUID(int=124)
            data = complete_data()
            data.pop("formal_claim")
            drafts = {
                key: {
                    "id": str(key),
                    "revision": 1,
                    "wizardState": "FORMAL_CLAIM",
                    "draftData": deepcopy(data),
                }
                for key in (DRAFT, other)
            }

            async def get_draft(draft_id, actor):
                core.authorize(actor)
                return deepcopy(drafts[draft_id])

            async def save_draft(draft_id, actor, **kwargs):
                core.authorize(actor)
                request = TelegramIntakeDraftUpdateRequest.model_validate(
                    {
                        "expectedRevision": kwargs["expected_revision"],
                        "wizardState": kwargs["wizard_state"],
                        "draftData": kwargs["draft_data"],
                    }
                )
                assert drafts[draft_id]["revision"] == request.expected_revision
                drafts[draft_id] = {
                    "id": str(draft_id),
                    "revision": request.expected_revision + 1,
                    "wizardState": request.wizard_state,
                    "draftData": deepcopy(request.draft_data),
                }
                return deepcopy(drafts[draft_id])

            core.get_intake_draft = get_draft
            core.save_intake_draft = save_draft
            await dispatch(f"case:draft:{DRAFT}")
            first_prompt = len(sent) + 100
            await dispatch(f"case:draft:{other}")
            before = deepcopy(drafts)
            if old_button:
                await dispatch_raw(app, content="case:formal:no", message_id=first_prompt)
                assert drafts == before, "An old V1 card answered a different V1 draft"
                assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_ID_KEY] == str(other)
            else:
                await dispatch("case:formal:no")
                assert drafts[DRAFT] == before[DRAFT]
                assert drafts[other]["draftData"]["formal_claim"] == "NO"
                assert drafts[other]["wizardState"] == "CONFIRM"
                assert "intakeVersion" not in drafts[other]["draftData"]

    asyncio.run(scenario())
