# ruff: noqa: RUF001
"""Activated answer extension with actual PTB application and actor-serial processor."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import patch
from uuid import UUID, uuid5

import pytest
from legal_core.factual_safety_intake import SCREENING_VERSION
from legal_core.intake_answer_contracts import AnswerQuestion, derive_answer_question
from legal_core.intake_answer_values import apply_answer_value
from legal_core.intake_answer_wire import AnswerStatusResponse
from telegram_gateway import bot
from telegram_gateway.answer_intent import bound_callback
from telegram_gateway.answer_review import review_blocks
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.understanding_client import canonical_hash
from telegram_gateway.update_processor import ActorSerialUpdateProcessor
from test_case_experience import ExperienceCore, harness
from test_dialog_isolation import ACTOR, DRAFT


class AnswerCore(ExperienceCore):
    def __init__(self):
        super().__init__()
        self.draft = {"id": str(DRAFT), "revision": 1, "wizardState": "INCOMING",
                      "draftData": {"intakeVersion": 2}}
        self.operations, self.starts, self.accepts, self.cancels = {}, [], [], []
        self.lose_accept = self.lose_cancel = False

    async def _json_request(self, method, path, *, telegram_user_id, **kwargs):
        assert telegram_user_id == ACTOR
        if path.endswith("/understanding") and method == "GET":
            return {"operation": None}
        if path.endswith("/answer-interpretations"):
            matches = [record["status"] for record in self.operations.values()
                       if record["revision"] == self.draft["revision"]
                       and record["status"]["state"] in {"PENDING", "PROPOSED", "UNRESOLVED"}]
            if method == "GET":
                return {"operation": deepcopy(matches[0]) if matches else None}
            assert method == "POST" and not matches
            assert kwargs["payload"]["expectedRevision"] == self.draft["revision"]
            operation = uuid5(DRAFT, str(len(self.starts)))
            self.operations[operation] = {
                "revision": self.draft["revision"], "input": deepcopy(self.draft),
                "answer": kwargs["payload"]["answer"],
                "status": {"operationId": str(operation), "state": "PENDING", "question": None,
                           "interpretation": None, "source": None, "acceptance": None},
            }
            self.starts.append(operation)
            return deepcopy(self.operations[operation]["status"])
        if "/intake-answer-preparations/" not in path:
            return await super()._json_request(method, path, telegram_user_id=telegram_user_id,
                                               **kwargs)
        operation = UUID(path.split("/")[3])
        record, status = self.operations[operation], self.operations[operation]["status"]
        if method == "GET":
            return deepcopy(status)
        if method == "DELETE":
            assert status["state"] != "ACCEPTED"
            status.update(state="MANUAL_CONTINUATION", question=None, interpretation=None,
                          source=None)
            self.cancels.append(operation)
            if self.lose_cancel:
                self.lose_cancel = False
                raise LegalCoreApiError(503, "UNAVAILABLE", "unknown")
            return deepcopy(status)
        assert method == "POST" and path.endswith("/acceptance")
        assert status["state"] == "PROPOSED"
        assert self.draft["revision"] == record["revision"]
        question, value = status["question"], status["interpretation"]["value"]
        self.draft["draftData"] = apply_answer_value(
            self.draft["draftData"], AnswerQuestion.model_validate(question),
            value, record["answer"],
        )
        self.draft["revision"] += 1
        self.accepts.append(operation)
        status.update(state="ACCEPTED", question=None, interpretation=None, source=None,
                      acceptance={"draftId": str(DRAFT),
                                  "acceptedRevision": self.draft["revision"]})
        if self.lose_accept:
            self.lose_accept = False
            raise LegalCoreApiError(503, "UNAVAILABLE", "unknown")
        return {"operationId": str(operation), "acceptedRevision": self.draft["revision"],
                "draft": deepcopy(self.draft)}

    def propose(self, value, *, reason=None):
        operation = self.starts[-1]
        record = self.operations[operation]
        if record["status"]["state"] != "PENDING":
            return
        interpretation = {"status": "UNRESOLVED" if reason else "PROPOSED", "value": value,
                          "sourceSpans": [] if reason else [{"start": 0, "end": 1}],
                          "reasonCode": reason}
        question = derive_answer_question(record["input"]["draftData"],
                                          record["input"]["wizardState"])
        record["status"].update(
            state=interpretation["status"], interpretation=interpretation,
            question=question.model_dump(mode="json", by_alias=True),
            source={"draftId": str(DRAFT), "draftRevision": record["revision"],
                    "inputSha256": canonical_hash({key: record["input"][key]
                                                   for key in ("draftData", "wizardState")}),
                    "proposalSha256": canonical_hash(interpretation)},
        )


@asynccontextmanager
async def answer_harness(saved_core=None):
    import test_case_experience as original
    factory = original.build_application_with_case_experience

    def build(token):
        return factory(token, enable_understanding=True, enable_answer_interpretation=True)

    with patch.object(original, "build_application_with_case_experience", build):
        async with harness() as (app, _core, sent, dispatch):
            core = saved_core or AnswerCore()
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = core
            yield app, core, sent, dispatch


@pytest.mark.parametrize("state,extra", [
    ("INCOMING", {}), ("SITUATION", {}),
    ("SERVICES", {"situationAreas": ["TREATMENT"]}), ("EVENT", {}), ("EVENT_DATE", {}),
    ("CHRONOLOGY", {}), ("CLINIC_ACTIONS", {}), ("HEALTH", {}), ("SAFETY", {}),
    ("ANALYSIS_CLARIFICATION", {
        "analysisClarificationRequests": [{"questionId": "RESPONSE_PURPOSE"}],
    }),
])
def test_every_factual_state_intercepts_free_text_before_ordinary_parser_or_put(state, extra):
    async def scenario():
        async with answer_harness() as (app, core, _sent, dispatch):
            core.draft.update(wizardState=state,
                              draftData={"intakeVersion": 2, **extra})
            # Directly resume saved explicit question without generic missing-state normalization.
            app.user_data[ACTOR][bot.WIZARD_DATA_KEY] = bot._saved_draft_selection(
                DRAFT, core.draft,
            )
            await dispatch("text:Синтетический ответ на текущий вопрос")
            assert len(core.starts) == 1 and not core.saved and not core.jobs and not core.accepts
            assert core.operations[core.starts[0]]["answer"] == (
                "Синтетический ответ на текущий вопрос"
            )
    asyncio.run(scenario())


def test_pending_refresh_consent_and_lost_accept_response_resume_once_after_ram_restart():
    async def scenario():
        async with answer_harness() as (app, core, sent, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:Пациент недоволен")
            operation = core.starts[-1]
            assert not core.saved
            core.propose("COMPLAINT")
            app.user_data[ACTOR].clear()
            await dispatch(bound_callback("refresh", operation, DRAFT))
            assert any("обращение с недовольством" in item.get("text", "") for item in sent)
            assert "incomingKind" not in core.draft["draftData"]
            core.lose_accept = True
            await dispatch(bound_callback("accept", operation, DRAFT))
            await dispatch(bound_callback("refresh", operation, DRAFT))
            assert core.accepts == [operation]
            assert core.draft["draftData"]["incomingKind"] == "COMPLAINT"
            assert core.draft["wizardState"] == "SITUATION"
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SITUATION"
            assert not core.jobs
    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["AMBIGUOUS", "CONTRADICTS_CONFIRMED", "OFF_QUESTION"])
def test_unresolved_has_fixed_copy_and_no_accept_button_or_invented_unknown(reason):
    async def scenario():
        async with answer_harness() as (_app, core, sent, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:Всё как раньше")
            operation = core.starts[-1]
            core.propose(None, reason=reason)
            await dispatch(bound_callback("refresh", operation, DRAFT))
            assert "incomingKind" not in core.draft["draftData"]
            assert not core.accepts and not core.saved
            assert all(button.callback_data != bound_callback("accept", operation, DRAFT)
                       for row in sent[-1]["reply_markup"].inline_keyboard for button in row)
            assert reason not in sent[-1]["text"] and "Hermes" not in sent[-1]["text"]
            assert "Что поступило от пациента" in sent[-1]["text"]
    asyncio.run(scenario())


def test_menu_and_lost_cancel_response_fence_operation_before_clearing_ram():
    async def scenario():
        async with answer_harness() as (app, core, _sent, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:Пациент недоволен")
            operation = core.starts[-1]
            core.lose_cancel = True
            await dispatch("/menu")
            assert core.cancels == [operation]
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            core.propose("COMPLAINT")
            assert core.operations[operation]["status"]["state"] == "MANUAL_CONTINUATION"
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["case:v2:single:incomingKind:COMPLAINT", "case:v2:back"])
def test_factual_button_and_back_cancel_pending_before_ordinary_write(action):
    async def scenario():
        async with answer_harness() as (_app, core, _sent, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:Пациент недоволен")
            operation = core.starts[-1]
            await dispatch(action)
            assert core.cancels == [operation]
            core.propose("FORMAL_DOCUMENT")
            assert core.operations[operation]["status"]["state"] == "MANUAL_CONTINUATION"
            if action.endswith("COMPLAINT"):
                assert core.draft["draftData"]["incomingKind"] == "COMPLAINT"
    asyncio.run(scenario())


def test_actual_actor_processor_keeps_menu_responsive_while_synthetic_model_is_blocked():
    async def scenario():
        async with answer_harness() as (app, core, _sent, dispatch):
            assert isinstance(app.update_processor, ActorSerialUpdateProcessor)
            process = type(app).process_update

            async def serial(self, update):
                return await self.update_processor.process_update(update, process(self, update))

            released = asyncio.Event()
            model = asyncio.create_task(released.wait())
            try:
                with patch.object(type(app), "process_update", serial):
                    await dispatch(f"case:draft:{DRAFT}")
                    await dispatch("text:Пациент недоволен")
                    operation = core.starts[-1]
                    await dispatch("/menu")
                assert not released.is_set() and not model.done()
                assert core.cancels == [operation] and not core.jobs
            finally:
                released.set()
                await model
    asyncio.run(scenario())


def test_restart_discovery_resumes_pending_without_reposting_answer():
    async def scenario():
        async with answer_harness() as (app, core, _sent, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:Пациент недоволен")
            operation = core.starts[-1]
            app.user_data[ACTOR].clear()
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch(bound_callback("edit", operation, DRAFT))
            await dispatch("text:Пришёл документ от пациента")
            assert len(core.starts) == 2 and core.cancels == [operation]
            assert "incomingKind" not in core.draft["draftData"]
    asyncio.run(scenario())


def test_fresh_application_with_no_user_data_recovers_only_durable_answer_operation():
    async def scenario():
        async with answer_harness() as (_app, core, _sent, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:Пациент недоволен")
            operation = core.starts[-1]
        core.propose("COMPLAINT")
        async with answer_harness(core) as (app, _core, _sent, dispatch):
            assert not app.user_data
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch(bound_callback("accept", operation, DRAFT))
            assert len(core.starts) == 1 and core.accepts == [operation]
            assert core.draft["draftData"]["incomingKind"] == "COMPLAINT"
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SITUATION"
    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["MATERIALS", "SUMMARY", "V2_CONFIRM"])
def test_documents_summary_and_final_consent_are_not_interpreted(state):
    async def scenario():
        async with answer_harness() as (app, core, _sent, dispatch):
            core.draft.update(wizardState=state)
            app.user_data[ACTOR][bot.WIZARD_DATA_KEY] = bot._saved_draft_selection(
                DRAFT, core.draft,
            )
            await dispatch("text:Да, всё верно")
            assert not core.starts and not core.accepts and not core.jobs
    asyncio.run(scenario())


def test_explicit_unknown_date_is_saved_only_after_consent_and_not_reasked():
    async def scenario():
        async with answer_harness() as (app, core, _sent, dispatch):
            core.draft.update(wizardState="EVENT_DATE", draftData={
                "intakeVersion": 2, "incomingKind": "MESSAGE_OR_REQUEST",
                "incomingSourceStatus": "NOT_ATTACHED", "situationAreas": ["SERVICE"],
                "eventSummary": "Синтетическая ситуация с ожиданием приёма",
            })
            app.user_data[ACTOR][bot.WIZARD_DATA_KEY] = bot._saved_draft_selection(
                DRAFT, core.draft,
            )
            await dispatch("text:Я не знаю точную дату")
            operation = core.starts[-1]
            core.propose({"date": None, "precision": "UNKNOWN"})
            await dispatch(bound_callback("refresh", operation, DRAFT))
            assert "eventDate" not in core.draft["draftData"]
            await dispatch(bound_callback("accept", operation, DRAFT))
            assert core.draft["draftData"]["eventDate"] == {
                "date": None, "precision": "UNKNOWN"}
            assert core.draft["wizardState"] == "CHRONOLOGY"
            assert len(core.starts) == 1
    asyncio.run(scenario())


def test_legacy_draft_navigation_does_not_enter_v2_answer_boundary():
    async def scenario():
        async with answer_harness() as (app, core, sent, dispatch):
            core.draft.update(wizardState="SERVICE_TYPE",
                              draftData={"incident_type": "QUALITY_COMPLAINT"})
            app.user_data[ACTOR][bot.WIZARD_DATA_KEY] = bot._saved_draft_selection(
                DRAFT, core.draft, is_v2=False,
            )
            await dispatch("/menu")
            assert not core.starts and not core.cancels
            assert any("photo" in item for item in sent)
    asyncio.run(scenario())


def test_review_uses_canonical_preview_and_discloses_reset_dependencies_before_consent():
    core = AnswerCore()
    core.draft.update(wizardState="HEALTH", draftData={
        "intakeVersion": 2, "healthSignals": ["UNKNOWN"],
        "safetyScreening": {"schemaVersion": SCREENING_VERSION,
                            "hospitalizationReported": "UNKNOWN"},
    })

    async def scenario():
        await core._json_request(
            "POST", f"/v1/telegram-intake-drafts/{DRAFT}/answer-interpretations",
            telegram_user_id=ACTOR,
            payload={"expectedRevision": core.draft["revision"], "answer": "Госпитализирован"},
        )
        core.propose(["HOSPITALIZATION"])

    asyncio.run(scenario())
    status = AnswerStatusResponse.model_validate(core.operations[core.starts[-1]]["status"])
    text = "\n".join(review_blocks(status, core.draft["draftData"]))
    assert "заново уточнить" in text
    assert "последствиях и требованиях" in text
    assert core.draft["draftData"]["safetyScreening"]["hospitalizationReported"] == "UNKNOWN"


def test_new_text_does_not_silently_replace_pending_answer_and_copy_explains_it():
    async def scenario():
        async with answer_harness() as (_app, core, sent, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:Пациент недоволен")
            await dispatch("text:Я хочу заменить предыдущий ответ")
            assert len(core.starts) == 1
            assert core.operations[core.starts[-1]]["answer"] == "Пациент недоволен"
            assert any("Новый ответ пока не сохранён" in item.get("text", "") for item in sent)
    asyncio.run(scenario())
