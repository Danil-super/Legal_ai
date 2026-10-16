"""Independent actual-PTB probes with a synthetic Core; no network or database."""

import asyncio
from copy import deepcopy
from uuid import uuid4

from telegram.ext import ConversationHandler
from telegram_gateway import bot
from telegram_gateway.answer_intent import bound_callback
from telegram_gateway.answer_runtime import controls, route_input
from telegram_gateway.case_experience_runtime import build_application_with_case_experience
from telegram_gateway.case_wizard import LegalCoreApiError
from test_answer_runtime import AnswerCore, answer_harness
from test_dialog_isolation import ACTOR, DRAFT


def handlers(app):
    return [handler for group in app.handlers.values() for handler in group]


def intake(app):
    return next(handler for handler in handlers(app) if isinstance(handler, ConversationHandler)
                and handler.name == "administrator-case-intake-v1")


def test_independent_enabling_full_app_does_not_mutate_default_or_initial_only_apps():
    token = "123456:" + "q" * 40
    ordinary = build_application_with_case_experience(token)
    initial = build_application_with_case_experience(token, enable_understanding=True)
    entries = tuple(intake(ordinary).entry_points)
    callbacks = tuple(entry.callback for entry in entries)
    full = build_application_with_case_experience(
        token, enable_understanding=True, enable_answer_interpretation=True,
    )
    after = build_application_with_case_experience(token)
    assert any(getattr(handler, "callback", None) is route_input for handler in handlers(full))
    assert any(entry.callback is controls for entry in intake(full).entry_points)
    for app in (ordinary, initial, after):
        assert not any(getattr(handler, "callback", None) is route_input
                       for handler in handlers(app))
        assert not any(entry.callback is controls for entry in intake(app).entry_points)
    assert tuple(intake(ordinary).entry_points) == entries
    assert tuple(entry.callback for entry in intake(ordinary).entry_points) == callbacks


def test_independent_lost_start_response_recovers_pending_operation_without_second_post():
    class LostStartCore(AnswerCore):
        async def _json_request(self, method, path, **kwargs):
            result = await super()._json_request(method, path, **kwargs)
            if method == "POST" and path.endswith("/answer-interpretations"):
                raise LegalCoreApiError(503, "UNAVAILABLE", "unknown")
            return result

    async def scenario():
        core = LostStartCore()
        async with answer_harness(core) as (app, _, sent, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:A synthetic factual answer.")
            assert len(core.starts) == 1 and not core.accepts and not core.jobs
            operation = core.starts[0]
            app.user_data[ACTOR].clear()
            await dispatch(f"case:draft:{DRAFT}")
            assert len(core.starts) == 1
            assert core.operations[operation]["status"]["state"] == "PENDING"
            assert any("reply_markup" in item for item in sent)

    asyncio.run(scenario())


def test_independent_unapplied_delete_failure_does_not_clear_draft_or_claim_cancellation():
    class RefusingCancelCore(AnswerCore):
        deny_cancel = True

        async def _json_request(self, method, path, **kwargs):
            if method == "DELETE" and self.deny_cancel:
                raise LegalCoreApiError(503, "UNAVAILABLE", "unknown")
            return await super()._json_request(method, path, **kwargs)

    async def scenario():
        core = RefusingCancelCore()
        async with answer_harness(core) as (app, _, sent, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:A synthetic factual answer.")
            operation = core.starts[0]
            before = deepcopy(core.draft)
            await dispatch("/menu")
            assert not core.cancels and not core.accepts and not core.jobs
            assert core.draft == before
            assert bot.WIZARD_DATA_KEY in app.user_data[ACTOR]
            assert core.operations[operation]["status"]["state"] == "PENDING"
            assert not any("Разбор остановлен" in item.get("text", "") for item in sent)
            core.deny_cancel = False
            await dispatch("/menu")
            assert core.cancels == [operation]
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]

    asyncio.run(scenario())


def test_independent_wrong_draft_selector_cannot_accept_an_owned_other_draft_operation():
    async def scenario():
        async with answer_harness() as (_, core, _, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:An unhappy patient message.")
            operation = core.starts[0]
            core.propose("COMPLAINT")
            before = deepcopy(core.draft)
            await dispatch(bound_callback("accept", operation, uuid4()))
            assert core.draft == before and not core.accepts and not core.cancels

    asyncio.run(scenario())


def test_independent_old_accepted_refresh_preserves_later_explicit_navigation_checkpoint():
    async def scenario():
        async with answer_harness() as (app, core, _, dispatch):
            await dispatch(f"case:draft:{DRAFT}")
            await dispatch("text:An unhappy patient message.")
            operation = core.starts[0]
            core.propose("COMPLAINT")
            await dispatch(bound_callback("accept", operation, DRAFT))
            core.draft["revision"] += 1
            core.draft["wizardState"] = "HEALTH"
            before = deepcopy(core.draft)
            await dispatch(bound_callback("refresh", operation, DRAFT))
            assert core.draft == before
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "HEALTH"
            assert core.accepts == [operation] and not core.jobs

    asyncio.run(scenario())
