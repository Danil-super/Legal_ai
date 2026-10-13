"""Production composition regressions with real versioned Core request contracts.

All descriptions are fixed synthetic examples. Telegram and Core network I/O are
the only boundaries replaced; the PTB handlers and draft validators are real.
"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from uuid import UUID

import pytest
from legal_core.api_contracts import (
    FactInput,
    TelegramIntakeDraftCreateRequest,
    TelegramIntakeDraftUpdateRequest,
)
from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_wizard import LegalCoreApiError, LegalCoreClient
from telegram_gateway.factual_safety_intake import screening_question
from test_case_experience import harness
from test_dialog_isolation import ACTOR, DRAFT

DESCRIPTION = "Пациент сообщил про скол коронки после лечения и попросил обсудить ситуацию."
OTHER_ACTOR = ACTOR + 1
V2 = "dental-case-intake.v2"
V1 = "dental-case-intake.v1"
CASE = UUID("00000000-0000-0000-0000-000000000456")
SOURCE_HASH = "a" * 64


class VersionedCore:
    """In-memory boundary using the same create/update validators as Core REST."""

    def __init__(self):
        self.draft = None
        self.creates = []
        self.saved = []
        self.submissions = []
        self.replays = {}
        self.deny = False
        self.fail_once = False
        self.source_version = V2
        self.source = {"eventSummary": DESCRIPTION, "caseId": str(CASE)}
        self.clarification_creates = []

    def authorize(self, actor):
        if self.deny or actor != ACTOR:
            raise LegalCoreApiError(403, "ACTOR_NOT_AUTHORIZED", "denied")

    async def get_actor(self, actor):
        self.authorize(actor)
        return {"role": "CLINIC_OWNER"}

    async def _json_request(self, method, path, *, telegram_user_id, idempotency_key, payload):
        self.authorize(telegram_user_id)
        key = (method, path, idempotency_key)
        if key in self.replays:
            return deepcopy(self.replays[key])
        if method == "POST" and path == "/v1/telegram-intake-drafts":
            request = TelegramIntakeDraftCreateRequest.model_validate(payload)
            self.creates.append(deepcopy(payload))
            is_v2 = request.intake_schema_version == V2
            self.draft = {
                "id": str(DRAFT), "revision": 1,
                "wizardState": "INCOMING" if is_v2 else "INCIDENT",
                "draftData": {"intakeVersion": 2} if is_v2 else {},
            }
        elif method == "PUT" and path == f"/v1/telegram-intake-drafts/{DRAFT}":
            request = TelegramIntakeDraftUpdateRequest.model_validate(payload)
            assert self.draft is not None
            if request.expected_revision != self.draft["revision"]:
                raise LegalCoreApiError(409, "INTAKE_DRAFT_REVISION_CONFLICT", "stale")
            self.saved.append(request.model_dump(mode="json", by_alias=True))
            self.draft = {
                "id": str(DRAFT), "revision": request.expected_revision + 1,
                "wizardState": request.wizard_state, "draftData": deepcopy(request.draft_data),
            }
        else:
            raise AssertionError(f"unexpected boundary request: {method} {path}")
        result = deepcopy(self.draft)
        self.replays[key] = result
        if method == "PUT" and self.fail_once:
            self.fail_once = False
            raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "ambiguous save")
        return deepcopy(result)

    create_intake_draft = LegalCoreClient.create_intake_draft
    save_intake_draft = LegalCoreClient.save_intake_draft

    async def get_intake_draft(self, draft_id, actor):
        self.authorize(actor)
        assert draft_id == DRAFT and self.draft is not None
        return deepcopy(self.draft)

    async def submit_workflow(self, workflow_id, facts, actor, *, intake_schema_version=V1):
        self.authorize(actor)
        assert workflow_id == DRAFT
        for fact in facts:
            FactInput.model_validate(fact)
        self.submissions.append((intake_schema_version, deepcopy(facts)))
        # Report generation is outside this intake regression: preserve the draft
        # on a recoverable downstream failure after capturing the validated request.
        raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "synthetic report unavailable")

    async def clarification_preview(self, case_id, actor):
        self.authorize(actor)
        assert case_id == CASE
        if self.source_version == V1:
            raise LegalCoreApiError(409, "CLARIFICATION_SCHEMA_UNSUPPORTED", "new guided case")
        return {"caseId": str(case_id), "expectedFactSnapshotSha256": SOURCE_HASH}

    async def create_clarification_draft(self, case_id, actor, *, expected_sha256, key):
        self.authorize(actor)
        assert (case_id, expected_sha256) == (CASE, SOURCE_HASH)
        self.clarification_creates.append((case_id, expected_sha256, key))
        data = {
            "intakeVersion": 2, "eventSummary": DESCRIPTION,
            "correctionSource": {"caseId": str(CASE), "factSnapshotSha256": SOURCE_HASH},
        }
        request = TelegramIntakeDraftUpdateRequest.model_validate({
            "expectedRevision": 1, "wizardState": "INCOMING", "draftData": data,
        })
        self.draft = {"id": str(DRAFT), "revision": 1,
                      "wizardState": request.wizard_state, "draftData": request.draft_data}
        return deepcopy(self.draft)


@asynccontextmanager
async def versioned_harness():
    async with harness() as (app, _legacy_core, sent, dispatch):
        core = VersionedCore()
        app.bot_data["legal_core_client"] = core
        yield app, core, sent, dispatch


async def start_path(app, core, dispatch, path):
    if path == "quick":
        await dispatch("quick:start")
        await dispatch("text:" + DESCRIPTION)
        pending = app.user_data[ACTOR]["quick_intake_candidate"]
        await dispatch(f"quick:review:{pending['nonce']}:accept")
        assert core.draft["draftData"].get("intakeVersion") == 2, (
            "The one-message path must create a versioned V2 draft, like case:start"
        )
        assert core.draft["draftData"]["eventSummary"] == DESCRIPTION
        await dispatch(f"case:draft:{DRAFT}")
    else:
        await dispatch("case:start")
    assert core.creates == [{"intakeSchemaVersion": V2}]


async def collect_until_safety(core, dispatch, *, area, event_date, health):
    # Both entry paths use the same factual V2 questions. The quick description
    # may already supply EVENT, but all other facts below are explicitly answered.
    answers = {
        "INCOMING": "case:v2:single:incomingKind:COMPLAINT",
        "SITUATION": f"case:v2:multi:situationAreas:{area}",
        "SERVICES": "text:Установка коронки; коррекция коронки",
        "EVENT": "text:" + DESCRIPTION,
        "EVENT_DATE": "text:" + event_date,
        "CHRONOLOGY": "case:v2:single:conflictStage:UNKNOWN",
        "CLINIC_ACTIONS": "case:v2:multi:clinicActions:INVITED_FOR_EXAMINATION",
        "HEALTH": f"case:v2:multi:healthSignals:{health}",
        "MATERIALS": "case:v2:material:skip",
    }
    observed = []
    for _ in range(12):
        state = core.draft["wizardState"]
        if state == "SAFETY":
            break
        assert state in answers, f"Unexpected intake state {state}"
        observed.append(state)
        await dispatch(answers[state])
        if state in {"SITUATION", "CLINIC_ACTIONS", "HEALTH"}:
            field = {"SITUATION": "situationAreas", "CLINIC_ACTIONS": "clinicActions",
                     "HEALTH": "healthSignals"}[state]
            await dispatch(f"case:v2:multi:{field}:DONE")
    assert core.draft["wizardState"] == "SAFETY"
    assert ("SERVICES" in observed) == (area == "TREATMENT")
    data = core.draft["draftData"]
    assert data["eventSummary"] == DESCRIPTION
    assert data["conflictStage"] == "UNKNOWN"
    assert data["healthSignals"] == [health]
    assert not set(data) & {"formal_claim", "harm_claimed", "lawyer_contact", "regulator_or_court"}


@pytest.mark.parametrize("path", ["quick", "manual"])
def test_new_drafts_explicitly_request_v2_and_preserve_one_message(path):
    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            await start_path(app, core, dispatch, path)
            if path == "quick":
                assert "safetyScreening" not in core.draft["draftData"], (
                    "Missing facts in the description must remain unanswered, not NO"
                )
    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["quick", "manual"])
@pytest.mark.parametrize("area", ["TREATMENT", "SERVICE"])
@pytest.mark.parametrize("case", ["exact", "unknown_date", "unknown_safety", "urgent_unknown_date"])
def test_shared_v2_services_dates_safety_summary_and_confirmation(path, area, case):
    async def scenario():
        async with versioned_harness() as (app, core, sent, dispatch):
            await start_path(app, core, dispatch, path)
            urgent = case == "urgent_unknown_date"
            date_unknown = case in {"unknown_date", "urgent_unknown_date"}
            await collect_until_safety(
                core, dispatch, area=area,
                event_date="неизвестно" if date_unknown else "01.09.2026",
                health="HOSPITALIZATION" if urgent else "UNKNOWN",
            )
            assert core.submissions == []
            if urgent:
                await dispatch("case:safety:known:confirm")
            else:
                for _ in range(8):
                    question = screening_question(core.draft["draftData"])
                    if question is None:
                        break
                    field = question[0]
                    answer = "UNKNOWN" if case == "unknown_safety" and (
                        field == "representativeContact"
                    ) else "NO"
                    await dispatch(f"case:safety:{field}:{answer}")
            assert core.draft["wizardState"] == "SUMMARY"
            assert any("Краткая фабула" in message.get("text", "") for message in sent)
            assert core.submissions == [], "Summary must be reviewed before creating a case"
            if urgent:
                screening = core.draft["draftData"]["safetyScreening"]
                assert screening["representativeContact"] == "UNKNOWN"
            await dispatch("case:v2:summary:confirm")
            if case == "unknown_safety":
                assert core.draft["wizardState"] == "SAFETY"
                assert screening_question(core.draft["draftData"])[0] == "representativeContact"
                assert core.submissions == []
                return
            assert core.draft["wizardState"] == "V2_CONFIRM"
            await dispatch(f"case:confirm:{DRAFT}")
            if case == "unknown_date":
                assert core.draft["wizardState"] == "EVENT_DATE"
                assert core.submissions == []
            else:
                assert len(core.submissions) == 1
                assert core.submissions[0][0] == V2
                summary_fact = next(item for item in core.submissions[0][1]
                                    if item["factKey"] == "EVENT_SUMMARY")
                assert summary_fact["value"] == {"text": DESCRIPTION}
    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["quick", "manual"])
def test_back_menu_resume_and_stale_callbacks_keep_durable_v2_answers(path):
    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            await start_path(app, core, dispatch, path)
            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            await dispatch("case:v2:multi:situationAreas:TREATMENT")
            await dispatch("case:v2:multi:situationAreas:DONE")
            assert core.draft["wizardState"] == "SERVICES"
            await dispatch("case:v2:back")
            assert core.draft["wizardState"] == "SITUATION"
            assert core.draft["draftData"]["situationAreas"] == ["TREATMENT"]
            await dispatch("case:v2:multi:situationAreas:DONE")
            await dispatch("text:Установка коронки")
            before = deepcopy(core.draft)
            await dispatch("/menu")
            assert gateway_bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            await dispatch("text:Это случайный текст в меню")
            await dispatch("quick:review:aaaaaaaaaaaa:accept")
            assert core.draft == before
            await dispatch(f"case:draft:{DRAFT}")
            assert app.user_data[ACTOR][gateway_bot.WIZARD_DATA_KEY]["affectedServices"] == [
                "Установка коронки"
            ]
            assert core.draft["draftData"]["intakeVersion"] == 2
            assert len(core.creates) == 1
    asyncio.run(scenario())


def test_quick_review_duplicate_and_ambiguous_retry_create_one_v2_draft():
    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + DESCRIPTION)
            pending = app.user_data[ACTOR]["quick_intake_candidate"]
            accept = f"quick:review:{pending['nonce']}:accept"
            core.fail_once = True
            await dispatch(accept)
            await dispatch(accept)
            await dispatch(accept)
            assert core.creates == [{"intakeSchemaVersion": V2}]
            assert len(core.saved) == 1
            assert core.draft["draftData"]["eventSummary"] == DESCRIPTION
    asyncio.run(scenario())


def test_quick_edit_replaces_description_and_invalidates_old_review_nonce():
    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + DESCRIPTION)
            old = app.user_data[ACTOR]["quick_intake_candidate"]["nonce"]
            await dispatch(f"quick:review:{old}:edit")
            await dispatch(f"quick:review:{old}:accept")
            assert core.creates == []
            replacement = "Пациент попросил выдать копии документов по проведённому лечению."
            await dispatch("text:" + replacement)
            current = app.user_data[ACTOR]["quick_intake_candidate"]["nonce"]
            assert current != old
            await dispatch(f"quick:review:{old}:accept")
            assert core.creates == []
            await dispatch(f"quick:review:{current}:accept")
            assert core.draft["draftData"] == {"intakeVersion": 2, "eventSummary": replacement}
    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["quick", "manual"])
def test_revoked_access_cannot_change_saved_answers_or_resume_as_another_actor(path):
    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            await start_path(app, core, dispatch, path)
            before = deepcopy(core.draft)
            await dispatch(f"case:draft:{DRAFT}", actor=OTHER_ACTOR)
            assert gateway_bot.WIZARD_DATA_KEY not in app.user_data[OTHER_ACTOR]
            assert core.draft == before
            core.deny = True
            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            assert core.draft == before
            assert core.submissions == []
    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["quick", "manual"])
def test_unauthorized_actor_and_group_updates_cannot_create_or_resume_drafts(path):
    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            if path == "quick":
                await dispatch("quick:start")
                await dispatch("text:" + DESCRIPTION)
                nonce = app.user_data[ACTOR]["quick_intake_candidate"]["nonce"]
                content = f"quick:review:{nonce}:accept"
            else:
                content = "case:start"
            await dispatch(content, actor=OTHER_ACTOR)
            await dispatch(content, chat_type="group")
            assert core.creates == [] and core.saved == []
            core.deny = True
            await dispatch(content)
            assert core.creates == [] and core.saved == []
            await dispatch(f"case:draft:{DRAFT}", actor=OTHER_ACTOR)
            assert gateway_bot.WIZARD_DATA_KEY not in app.user_data[OTHER_ACTOR]
    asyncio.run(scenario())


def test_v1_post_confirmation_clarification_offers_actionable_new_guided_case():
    async def scenario():
        async with versioned_harness() as (_app, core, sent, dispatch):
            core.source_version = V1
            before = deepcopy(core.source)
            await dispatch(f"case:clarify:{CASE}")
            message = sent[-1]
            markup = message.get("reply_markup")
            callbacks = {button.callback_data for row in markup.inline_keyboard for button in row}
            assert "case:start" in callbacks, "Unsupported V1 needs a working new-V2-case button"
            assert "нов" in message["text"].casefold()
            assert "повторное нажатие" not in message["text"].casefold()
            assert core.clarification_creates == [] and core.source == before
    asyncio.run(scenario())


def test_v2_post_confirmation_clarification_keeps_immutable_source_on_resume_and_edit():
    async def scenario():
        async with versioned_harness() as (app, core, _sent, dispatch):
            before = deepcopy(core.source)
            await dispatch(f"case:clarify:{CASE}")
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            assert core.draft["draftData"]["correctionSource"] == {
                "caseId": str(CASE), "factSnapshotSha256": SOURCE_HASH,
            }
            assert core.draft["draftData"]["eventSummary"] == DESCRIPTION
            assert core.source == before
            assert app.user_data[ACTOR][gateway_bot.WIZARD_DATA_KEY]["intakeVersion"] == 2
            assert core.creates == []
    asyncio.run(scenario())
