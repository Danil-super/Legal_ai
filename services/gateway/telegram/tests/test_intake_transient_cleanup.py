# ruff: noqa: RUF001
"""Real PTB dialogue: remove transient chat messages only after durable progress.

All text is synthetic. Only the Telegram transport and Core boundary are faked;
the production composition, watcher, actor fence and conversation handlers run.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from telegram.error import BadRequest, NetworkError
from telegram.ext import ApplicationHandlerStop, ExtBot
from telegram_gateway.case_wizard import LegalCoreApiError
from test_answer_runtime import AnswerCore
from test_confirmed_understanding_runtime import TEXT
from test_dialog_isolation import ACTOR, DRAFT
from test_intake_single_summary import simplified_harness


@asynccontextmanager
async def cleanup_harness():
    async with simplified_harness() as (app, core, sent, dispatch):
        original = ExtBot._post
        visible, deleted, events = {}, set(), []
        failures = {"question": False, "delete": False}
        delivery_error = NetworkError("Synthetic next-question delivery failure")

        async def expected_delivery_error(_update, context):
            if context.error is delivery_error:
                # The transport fault is intentional. Do not let the shared
                # harness's accumulating unexpected-error guard latch this one.
                raise ApplicationHandlerStop

        error_handlers = list(app.error_handlers.items())
        for handler, _block in error_handlers:
            app.remove_error_handler(handler)
        app.add_error_handler(expected_delivery_error)
        for handler, block in error_handlers:
            app.add_error_handler(handler, block=block)

        async def transport(self, endpoint, data=None, **kwargs):
            payload = dict(data or {})
            if endpoint == "deleteMessage":
                message_id = payload["message_id"]
                events.append(("delete", message_id, core.draft["revision"]))
                if failures["delete"]:
                    raise BadRequest("Synthetic message cannot be deleted")
                deleted.add(message_id)
                visible.pop(message_id, None)
                return True
            if endpoint == "sendMessage" and failures["question"] and (
                "С чем связана ситуация" in payload.get("text", "")
            ):
                events.append(("failed-send", None, core.draft["revision"]))
                raise delivery_error
            result = await original(self, endpoint, data=data, **kwargs)
            if endpoint in {"sendMessage", "editMessageText"}:
                message_id = result["message_id"]
                visible[message_id] = payload
                events.append(("send", message_id, core.draft["revision"]))
            return result

        with patch.object(ExtBot, "_post", transport):
            yield app, core, sent, dispatch, visible, deleted, events, failures


async def delivery_finished(app):
    async def finished():
        while app.bot_data.get("intake_delivery_tasks"):
            await asyncio.sleep(0.001)

    await asyncio.wait_for(finished(), timeout=2)


def message_with(visible, fragment):
    return next(message_id for message_id, payload in visible.items()
                if fragment in payload.get("text", ""))


def test_initial_wait_is_removed_after_next_prompt_but_original_description_is_retained():
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         events, _failures):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            waiting_id = message_with(visible, "Разбираю ситуацию")
            operation = core.starts[-1]
            core.propose(incomingKind="COMPLAINT")
            await delivery_finished(app)

            current_id = message_with(visible, "С чем связана ситуация")
            assert list(core.stages) == [operation]
            assert waiting_id not in visible, "obsolete waiting controls still remain in chat"
            assert current_id not in deleted
            assert 2 not in deleted, "the original narrative must remain in chat"
            assert core.draft["draftData"]["eventSummary"] == TEXT
            assert any(kind == "send" and message_id == current_id
                       for kind, message_id, _revision in events)
    asyncio.run(scenario())


def test_guided_button_answer_removes_saved_question_but_keeps_the_next_prompt():
    async def scenario():
        async with cleanup_harness() as (_app, core, _sent, dispatch, visible, deleted,
                                         _events, _failures):
            await dispatch("case:start")
            question_id = message_with(visible, "Что поступило от пациента")
            await dispatch("case:v2:single:incomingKind:COMPLAINT")

            current_id = message_with(visible, "С чем связана ситуация")
            assert core.draft["draftData"]["incomingKind"] == "COMPLAINT"
            assert question_id in deleted
            assert current_id not in deleted
    asyncio.run(scenario())


def test_guided_manual_short_answer_removes_saved_question_and_answer_only_after_next_prompt():
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         events, _failures):
            await dispatch("case:start")
            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            await dispatch("case:v2:multi:situationAreas:TREATMENT")
            await dispatch("case:v2:multi:situationAreas:DONE")
            question_id = message_with(visible, "Какие услуги затронуты")
            app.user_data[ACTOR]["answer_manual_once"] = {
                "draftId": str(DRAFT), "revision": core.draft["revision"],
            }
            before = len(events)
            await dispatch("text:Синтетическая услуга")  # Telegram incoming message 5.

            current_id = message_with(visible, "Кратко опишите, что произошло")
            assert core.draft["draftData"]["affectedServices"] == ["Синтетическая услуга"]
            assert {question_id, 5} <= deleted
            assert current_id not in deleted
            new_events = events[before:]
            sent_index = next(index for index, event in enumerate(new_events)
                              if event[:2] == ("send", current_id))
            assert all(index > sent_index for index, event in enumerate(new_events)
                       if event[0] == "delete")
    asyncio.run(scenario())


def test_guided_save_failure_keeps_question_and_short_answer():
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         _events, _failures):
            await dispatch("case:start")
            await dispatch("case:v2:single:incomingKind:COMPLAINT")
            await dispatch("case:v2:multi:situationAreas:TREATMENT")
            await dispatch("case:v2:multi:situationAreas:DONE")
            question_id = message_with(visible, "Какие услуги затронуты")
            app.user_data[ACTOR]["answer_manual_once"] = {
                "draftId": str(DRAFT), "revision": core.draft["revision"],
            }

            async def failed_save(*args, **kwargs):
                raise LegalCoreApiError(503, "UNAVAILABLE", "Synthetic save failure")

            core.save_intake_draft = failed_save
            await dispatch("text:Синтетическая услуга")

            assert "affectedServices" not in core.draft["draftData"]
            assert question_id not in deleted and 5 not in deleted
            assert question_id in visible
    asyncio.run(scenario())


def test_followup_cleanup_keeps_answer_until_saved_then_removes_old_question_answer_and_wait():
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         events, _failures):
            await dispatch(f"case:draft:{DRAFT}")
            question_id = message_with(visible, "Что поступило от пациента")
            await dispatch("text:Синтетический посетитель недоволен")
            waiting_id = message_with(visible, "Обрабатываю ответ")
            assert not deleted, "no cleanup before the interpretation is durably staged"
            operation = core.starts[-1]
            AnswerCore.propose(core, "COMPLAINT")
            await delivery_finished(app)

            current_id = message_with(visible, "С чем связана ситуация")
            assert list(core.stages) == [operation]
            assert core.draft["draftData"]["incomingKind"] == "COMPLAINT"
            assert {question_id, waiting_id, 2} <= deleted
            assert current_id not in deleted
            current_sent = next(index for index, event in enumerate(events)
                                if event[:2] == ("send", current_id))
            assert all(index > current_sent and revision > 1
                       for index, (kind, _message_id, revision) in enumerate(events)
                       if kind == "delete")
    asyncio.run(scenario())


def test_failed_staging_preserves_unsaved_short_answer_and_current_question():
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         _events, _failures):
            await dispatch(f"case:draft:{DRAFT}")
            question_id = message_with(visible, "Что поступило от пациента")
            await dispatch("text:Синтетический посетитель недоволен")
            request = core._json_request

            async def failed_stage(method, path, **kwargs):
                if method == "POST" and path.endswith("/draft-staging"):
                    raise LegalCoreApiError(503, "UNAVAILABLE", "Synthetic save failure")
                return await request(method, path, **kwargs)

            core._json_request = failed_stage
            AnswerCore.propose(core, "COMPLAINT")
            await delivery_finished(app)

            assert "incomingKind" not in core.draft["draftData"]
            assert not core.stages
            assert question_id not in deleted and 2 not in deleted
            assert question_id in visible
    asyncio.run(scenario())


def test_unresolved_followup_retires_wait_but_retains_unconfirmed_answer_for_correction():
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         _events, _failures):
            await dispatch(f"case:draft:{DRAFT}")
            question_id = message_with(visible, "Что поступило от пациента")
            await dispatch("text:Синтетическое неоднозначное сообщение")
            waiting_id = message_with(visible, "Обрабатываю ответ")
            AnswerCore.propose(core, None, reason="AMBIGUOUS")
            await delivery_finished(app)

            assert "incomingKind" not in core.draft["draftData"] and not core.stages
            assert waiting_id in deleted, "finished unresolved analysis must not look in-flight"
            assert question_id not in deleted and 2 not in deleted
    asyncio.run(scenario())


def test_failed_next_prompt_preserves_previous_question_and_short_answer_for_recovery():
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         events, failures):
            await dispatch(f"case:draft:{DRAFT}")
            question_id = message_with(visible, "Что поступило от пациента")
            await dispatch("text:Синтетический посетитель недоволен")
            failures["question"] = True
            AnswerCore.propose(core, "COMPLAINT")
            await delivery_finished(app)

            assert core.stages, "the simulated failure is after successful saving"
            assert any(event[0] == "failed-send" for event in events)
            assert question_id not in deleted and 2 not in deleted
            assert question_id in visible
    asyncio.run(scenario())


@pytest.mark.parametrize("initial", [True, False], ids=["description", "followup"])
def test_reopening_after_saved_result_and_failed_prompt_cleans_obsolete_wait(initial):
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         _events, failures):
            await dispatch("quick:start" if initial else f"case:draft:{DRAFT}")
            await dispatch("text:" + TEXT)
            waiting_id = message_with(visible, "Разбираю ситуацию" if initial else
                                      "Обрабатываю ответ")
            failures["question"] = True
            if initial:
                core.propose(incomingKind="COMPLAINT")
            else:
                AnswerCore.propose(core, "COMPLAINT")
            await delivery_finished(app)
            assert waiting_id not in deleted
            assert core.stages, "the interruption happened after durable staging"

            failures["question"] = False
            await dispatch(f"case:draft:{DRAFT}")

            assert waiting_id in deleted, "successful recovery must retire its own waiting card"
            assert message_with(visible, "С чем связана ситуация") not in deleted
            if initial:
                assert 2 not in deleted
            else:
                assert 2 in deleted
    asyncio.run(scenario())


def test_reopened_draft_cleans_its_new_wait_without_guessing_old_or_user_message_ids():
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         _events, _failures):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            operation = core.starts[-1]
            app.user_data[ACTOR].clear()  # A gateway restart lost ephemeral message bookkeeping.
            await dispatch(f"case:draft:{DRAFT}")
            waiting_id = max(message_id for message_id, payload in visible.items()
                             if "Разбираю ситуацию" in payload.get("text", ""))
            core.propose(incomingKind="COMPLAINT")
            await delivery_finished(app)

            assert core.starts == [operation]
            assert waiting_id not in visible
            assert 2 not in deleted
            assert message_with(visible, "С чем связана ситуация") not in deleted
    asyncio.run(scenario())


def test_late_result_after_menu_never_deletes_new_menu_or_original_narrative():
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         _events, _failures):
            await dispatch("quick:start")
            await dispatch("text:" + TEXT)
            await dispatch("/menu")
            menu_ids = set(visible)
            core.propose(incomingKind="COMPLAINT")
            await delivery_finished(app)

            assert not core.stages
            assert not (menu_ids & deleted)
            assert 2 not in deleted
    asyncio.run(scenario())


@pytest.mark.parametrize("initial", [True, False], ids=["description", "followup"])
def test_telegram_delete_refusal_does_not_block_next_question_or_rewrite_saved_facts(initial):
    async def scenario():
        async with cleanup_harness() as (app, core, _sent, dispatch, visible, deleted,
                                         _events, failures):
            await dispatch("quick:start" if initial else f"case:draft:{DRAFT}")
            await dispatch("text:" + TEXT)
            failures["delete"] = True
            if initial:
                core.propose(incomingKind="COMPLAINT")
            else:
                AnswerCore.propose(core, "COMPLAINT")
            await delivery_finished(app)

            assert core.draft["draftData"]["incomingKind"] == "COMPLAINT"
            assert core.stages
            assert message_with(visible, "С чем связана ситуация") not in deleted
    asyncio.run(scenario())
