"""An immutable preparation button enters the actual owned PTB question flow."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from telegram_gateway import bot
from telegram_gateway.analysis_jobs_runtime import JobSnapshot, job_message
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.clarification_intent import encode_preparation_callback
from test_analysis_clarification_runtime import (
    DATE_1,
    PAYMENT_0,
    PAYMENT_1,
    PURPOSE,
    requested_data,
    seed,
)
from test_answer_runtime import AnswerCore
from test_case_experience import harness
from test_dialog_isolation import ACTOR, DRAFT
from test_intake_single_summary import await_state, simplified_harness
from test_preparation_job_readers import _snapshot

CASE, OPERATION = uuid4(), uuid4()
SOURCE = {"caseId": str(CASE), "factSnapshotSha256": "a" * 64,
          "operationId": str(OPERATION), "operationSha256": "b" * 64}
PREVIEW = {"caseId": str(CASE), "expectedFactSnapshotSha256": "a" * 64,
           "expectedOperationId": str(OPERATION), "expectedOperationSha256": "b" * 64}


def configure(core):
    seed(core, requested_data([DATE_1]) | {
        "correctionSource": SOURCE,
        "analysisClarificationAnswers": {"answers": [
            PAYMENT_0 | {"value": "PAID"}, PAYMENT_1 | {"value": "PAID"},
            {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
             "value": {"date": "2020-06-01", "precision": "EXACT"}},
            PURPOSE | {"value": "ANSWER_REQUEST"},
        ]},
    })
    core.clarification_preview = AsyncMock(return_value=deepcopy(PREVIEW))
    core.create_clarification_draft = AsyncMock(return_value=deepcopy(core.draft))


def display_source(sent):
    payload = _snapshot()
    payload["caseId"] = str(CASE)
    payload["result"]["source"].update(caseId=str(CASE), operationId=str(OPERATION))
    payload["result"]["clarificationRequests"] = [DATE_1]
    text, markup = job_message(JobSnapshot.parse(payload))
    sent.append({"text": text, "reply_markup": markup})


def test_one_recovery_click_asks_actual_question_and_accepts_the_next_message():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            configure(core)
            display_source(sent)
            original = deepcopy(core.draft)
            await dispatch(encode_preparation_callback(CASE, OPERATION))
            assert "Когда была оказана услуга «протезирование»?" in sent[-1]["text"]
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_ID_KEY] == str(DRAFT)
            assert not any("Продолжить уточнение" in button.text
                           for message in sent if message.get("reply_markup")
                           for row in message["reply_markup"].inline_keyboard for button in row)
            assert not any("Черновик открыт" in message.get("text", "")
                           or "Заполненный черновик" in message.get("text", "")
                           for message in sent)
            await dispatch("text:01.06.2020")
            assert len(core.starts) == 1  # The real next message entered its question handler.
            AnswerCore.propose(core, {"date": "2020-06-01", "precision": "EXACT"})
            await await_state(app, "SUMMARY")
            assert core.draft["draftData"]["analysisClarificationAnswers"]["answers"] == [
                *original["draftData"]["analysisClarificationAnswers"]["answers"],
                DATE_1 | {"value": {"date": "2020-06-01", "precision": "EXACT"}},
            ]
            assert core.draft["draftData"]["eventSummary"] == original["draftData"]["eventSummary"]
            assert not core.jobs and not core.accepts
            callbacks = [button.callback_data for row in sent[-1]["reply_markup"].inline_keyboard
                         for button in row]
            assert callbacks.count("case:v2:summary:confirm") == 1
            assert not any(value.startswith("case:confirm:") for value in callbacks)
    asyncio.run(scenario())


def test_same_displayed_button_retry_keeps_the_same_core_intent_and_owned_question():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            configure(core)
            display_source(sent)
            callback = encode_preparation_callback(CASE, OPERATION)
            await dispatch(callback)
            first = core.create_clarification_draft.await_args
            await dispatch(callback)
            assert core.create_clarification_draft.await_args == first
            assert "Когда была оказана услуга «протезирование»?" in sent[-1]["text"]
            await dispatch("text:01.06.2020")
            AnswerCore.propose(core, {"date": "2020-06-01", "precision": "EXACT"})
            await await_state(app, "SUMMARY")
            assert len(core.saved) == 1
    asyncio.run(scenario())


def test_default_composition_keeps_the_existing_legacy_handoff():
    async def scenario():
        async with harness() as (_app, core, sent, dispatch):
            configure(core)
            await dispatch(encode_preparation_callback(CASE, OPERATION))
            assert any(button.text == "Продолжить уточнение"
                       for row in sent[-1]["reply_markup"].inline_keyboard for button in row)
            assert not core.saved
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["access-withdrawn", "wrong-id"])
def test_current_owned_read_is_required_after_draft_creation(failure):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            configure(core)
            if failure == "access-withdrawn":
                core.get_intake_draft = AsyncMock(side_effect=LegalCoreApiError(
                    403, "ACTOR_NOT_AUTHORIZED", "PRIVATE",
                ))
            else:
                core.get_intake_draft = AsyncMock(return_value=core.draft | {"id": str(uuid4())})
            await dispatch(encode_preparation_callback(CASE, OPERATION))
            assert core.create_clarification_draft.await_count == 1
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            assert not any("Когда была оказана" in message.get("text", "") for message in sent)
            await dispatch("text:01.06.2020")
            assert not core.saved
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["foreign-preview", "foreign-draft", "denied", "stale",
                                    "expired"])
def test_unavailable_or_foreign_source_cannot_open_an_owned_question(failure):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            configure(core)
            if failure == "foreign-preview":
                core.clarification_preview.return_value["caseId"] = str(uuid4())
            elif failure == "foreign-draft":
                core.create_clarification_draft.return_value["draftData"]["correctionSource"] = (
                    SOURCE | {"operationId": str(uuid4())}
                )
            else:
                code = {"denied": "ACTOR_NOT_AUTHORIZED", "stale": "CASE_PREPARATION_STALE",
                        "expired": "INTAKE_DRAFT_EXPIRED"}[failure]
                core.clarification_preview.side_effect = LegalCoreApiError(409, code, "PRIVATE")
            await dispatch(encode_preparation_callback(CASE, OPERATION))
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            assert not any("Когда была оказана" in message.get("text", "") for message in sent)
            assert "PRIVATE" not in sent[-1]["text"]
            await dispatch("text:01.06.2020")
            assert not core.saved
    asyncio.run(scenario())
