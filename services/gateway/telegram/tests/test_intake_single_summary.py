"""Simplified real PTB dialogue stages draft facts before one final summary consent."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import patch
from uuid import UUID

from legal_core.intake_answer_contracts import AnswerQuestion
from legal_core.intake_answer_values import apply_answer_value
from telegram_gateway import bot
from test_answer_runtime import AnswerCore
from test_case_experience import harness
from test_confirmed_understanding_runtime import TEXT, UnderstandingCore
from test_dialog_isolation import ACTOR, DRAFT


class StagingCore(UnderstandingCore, AnswerCore):
    """Draft staging is not an acceptance and never creates a case or report."""

    def __init__(self):
        super().__init__()
        self.stages = {}

    async def _json_request(self, method, path, *, telegram_user_id, **kwargs):
        if method == "GET" and path.endswith("/answer-interpretations"):
            assert telegram_user_id == ACTOR
            matches = [record["status"] for record in self.operations.values()
                       if "answer" in record and record["revision"] == self.draft["revision"]
                       and record["status"]["state"] in {"PENDING", "PROPOSED", "UNRESOLVED"}]
            return {"operation": deepcopy(matches[0]) if matches else None}
        if not path.endswith("/draft-staging"):
            return await super()._json_request(method, path, telegram_user_id=telegram_user_id,
                                               **kwargs)
        assert method == "POST" and telegram_user_id == ACTOR
        operation = UUID(path.split("/")[3])
        if operation in self.stages:
            return deepcopy(self.stages[operation])
        record = self.operations[operation]
        status, source = record["status"], record["status"]["source"]
        assert status["state"] == "PROPOSED" and status["acceptance"] is None
        assert self.draft["revision"] == source["draftRevision"]
        assert kwargs["payload"] == {
            "expectedRevision": source["draftRevision"],
            "expectedInputSha256": source["inputSha256"],
            "expectedProposalSha256": source["proposalSha256"],
        }
        if "/intake-answer-preparations/" in path:
            self.draft["draftData"] = apply_answer_value(
                self.draft["draftData"], AnswerQuestion.model_validate(status["question"]),
                status["interpretation"]["value"], record["answer"],
            )
        else:
            self.draft["draftData"].update({key: value
                                           for key, value in status["suggestions"].items()
                                           if key != "sourceSpans"})
        self.draft["revision"] += 1
        receipt = {"operationId": str(operation), "stagedRevision": self.draft["revision"],
                   "draft": deepcopy(self.draft)}
        self.stages[operation] = deepcopy(receipt)
        return receipt


@asynccontextmanager
async def simplified_harness():
    import test_case_experience as original
    factory = original.build_application_with_case_experience

    def build(token):
        return factory(token, enable_understanding=True, enable_answer_interpretation=True,
                       enable_simplified_dialogue=True)

    with patch.object(original, "build_application_with_case_experience", build):
        async with harness() as (app, _core, sent, dispatch):
            core = StagingCore()
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = core
            yield app, core, sent, dispatch


async def await_state(app, state):
    async def reached():
        while app.user_data[ACTOR].get(bot.WIZARD_DATA_KEY, {}).get(bot.DRAFT_STATE_KEY) != state:
            await asyncio.sleep(0.001)

    await asyncio.wait_for(reached(), timeout=2)


def assert_no_intermediate_consent_or_report(core, sent):
    assert not core.accepts and not core.jobs
    callbacks = [button.callback_data for message in sent if message.get("reply_markup")
                 for row in message["reply_markup"].inline_keyboard for button in row]
    assert not any(isinstance(value, str) and value.startswith(("answer:a1:",
                                                              "understanding:a1:"))
                   for value in callbacks)


def assert_automatic_progress_has_no_refresh_controls(sent):
    assert not any(button.text == "Проверить результат"
                   for message in sent if message.get("reply_markup")
                   for row in message["reply_markup"].inline_keyboard for button in row)


def test_simplified_quick_description_stages_and_opens_working_next_question_automatically():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            core.propose(incomingKind="COMPLAINT")

            await await_state(app, "SITUATION")

            assert list(core.stages) == [operation] and core.starts == [operation]
            assert core.draft["draftData"]["incomingKind"] == "COMPLAINT"
            assert_no_intermediate_consent_or_report(core, sent)
            assert_automatic_progress_has_no_refresh_controls(sent)
            await dispatch("case:v2:multi:situationAreas:TREATMENT")
            await dispatch("case:v2:multi:situationAreas:DONE")
            assert core.draft["draftData"]["situationAreas"] == ["TREATMENT"]
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SERVICES"
            assert_no_intermediate_consent_or_report(core, sent)
    asyncio.run(scenario())


def test_simplified_followup_answer_stages_and_asks_next_question_without_individual_consent():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:Пациент недоволен")
            operation = core.starts[-1]
            AnswerCore.propose(core, "COMPLAINT")

            await await_state(app, "SITUATION")

            assert list(core.stages) == [operation] and core.starts == [operation]
            assert core.draft["draftData"]["incomingKind"] == "COMPLAINT"
            assert_no_intermediate_consent_or_report(core, sent)
            assert_automatic_progress_has_no_refresh_controls(sent)
            await dispatch("case:v2:multi:situationAreas:TREATMENT")
            await dispatch("case:v2:multi:situationAreas:DONE")
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SERVICES"
            assert_no_intermediate_consent_or_report(core, sent)
    asyncio.run(scenario())


def test_simplified_menu_blocks_late_proposal_from_staging_or_reopening_question():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            core.propose(incomingKind="COMPLAINT")
            reading, release = asyncio.Event(), asyncio.Event()
            request = core._json_request

            async def delayed_get(method, path, **kwargs):
                result = await request(method, path, **kwargs)
                if method == "GET" and path == f"/v1/intake-preparations/{operation}" and (
                    not reading.is_set()
                ):
                    snapshot = deepcopy(result)
                    reading.set()
                    await release.wait()
                    return snapshot
                return result

            core._json_request = delayed_get
            try:
                await asyncio.wait_for(reading.wait(), timeout=2)
                await dispatch("/menu")
                after_navigation = len(sent)
                release.set()
                await asyncio.sleep(0.05)

                assert core.cancels == [operation] and not core.stages
                assert "incomingKind" not in core.draft["draftData"]
                assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
                assert len(sent) == after_navigation
                assert_no_intermediate_consent_or_report(core, sent)
            finally:
                release.set()
    asyncio.run(scenario())
