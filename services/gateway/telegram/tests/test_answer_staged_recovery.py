# ruff: noqa: RUF001
"""Real PTB amount entry must recover a committed stage after lost HTTP receipts.

Synthetic facts only. Core and Telegram are boundary fakes; production dispatch,
question binding, watcher, staging validation and recovery callbacks are real.
"""

import asyncio
from copy import deepcopy

import pytest
from legal_core.factual_safety_intake import SCREENING_FIELDS, SCREENING_VERSION
from telegram_gateway import bot
from telegram_gateway.answer_intent import bound_callback
from telegram_gateway.case_wizard import LegalCoreApiError
from test_answer_runtime import AnswerCore
from test_dialog_isolation import ACTOR, DRAFT
from test_intake_single_summary import simplified_harness
from test_intake_transient_cleanup import delivery_finished


def amount_draft():
    return {
        "intakeVersion": 2,
        "incomingKind": "COMPLAINT",
        "incomingSourceStatus": "NOT_ATTACHED",
        "situationAreas": ["SERVICE"],
        "eventSummary": "Синтетический посетитель просит возврат за ожидание приёма.",
        "eventDate": {"date": "2026-10-15", "precision": "EXACT"},
        "conflictStage": "FIRST",
        "clinicActions": ["INVITED_FOR_EXAMINATION"],
        "healthSignals": ["NO_KNOWN_INFORMATION"],
        "caseMaterialsStatus": "NOT_ATTACHED",
        "safetyScreening": {
            "schemaVersion": SCREENING_VERSION,
            **dict.fromkeys(SCREENING_FIELDS, "NO"),
            "moneyRequested": "YES",
        },
    }


async def enter_amount(app, core, dispatch):
    core.draft.update(wizardState="SAFETY", draftData=amount_draft())
    await dispatch(f"case:draft:{DRAFT}")
    assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SAFETY"
    await dispatch("text:10000")
    operation = core.starts[-1]
    assert core.operations[operation]["answer"] == "10000"
    assert "amount" not in core.draft["draftData"]["safetyScreening"]
    return operation


def lose_stage_receipts(core, *, commit):
    request = core._json_request
    attempts = []

    async def lost_receipt(method, path, **kwargs):
        if method == "POST" and path.endswith("/draft-staging"):
            attempts.append(deepcopy(kwargs["payload"]))
            if len(attempts) <= 2:
                if commit:
                    await request(method, path, **kwargs)
                raise LegalCoreApiError(503, "UNAVAILABLE", "Synthetic lost response")
        return await request(method, path, **kwargs)

    core._json_request = lost_receipt
    return attempts


@pytest.mark.parametrize("action", ["refresh", "edit", "manual", "menu"])
def test_amount_recovery_controls_work_after_staging_committed_but_both_receipts_were_lost(action):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            operation = await enter_amount(app, core, dispatch)
            attempts = lose_stage_receipts(core, commit=True)
            AnswerCore.propose(core, {"amountKopecks": 1_000_000, "currency": "RUB"})
            await delivery_finished(app)
            assert len(attempts) == 2 and attempts[0] == attempts[1]
            assert list(core.stages) == [operation]
            assert core.draft["draftData"]["safetyScreening"]["amount"] == {
                "amountKopecks": 1_000_000, "currency": "RUB",
            }
            before = len(sent)

            await dispatch(bound_callback(action, operation, DRAFT))

            new_text = "\n".join(message.get("text", "") for message in sent[before:])
            assert "Не удалось подтвердить результат действия" not in new_text, (
                "the recovery button loops into the same stale-proposal error"
            )
            assert len(core.starts) == 1 and not core.accepts and not core.jobs
            assert list(core.stages) == [operation], "recovery must not stage a second value"
            if action == "menu":
                assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
                assert "answer_interpretation" not in app.user_data[ACTOR]
            else:
                wizard = app.user_data[ACTOR][bot.WIZARD_DATA_KEY]
                assert wizard[bot.DRAFT_REVISION_KEY] == core.draft["revision"]
                if action == "refresh":
                    assert wizard[bot.DRAFT_STATE_KEY] == "SUMMARY"

    asyncio.run(scenario())


def fail_amount_operation(core, operation):
    """Mirror Core's FAILED wire status and its exclusion from discovery."""
    core.operations[operation]["status"] = {
        "operationId": str(operation), "state": "MANUAL_CONTINUATION",
        "question": None, "interpretation": None, "source": None, "acceptance": None,
    }


def test_failed_amount_interpretation_automatically_reopens_question_instead_of_dead_error():
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            operation = await enter_amount(app, core, dispatch)
            before, revision = len(sent), core.draft["revision"]
            fail_amount_operation(core, operation)

            await delivery_finished(app)

            new_text = "\n".join(message.get("text", "") for message in sent[before:])
            assert "Не удалось подтвердить результат действия" not in new_text
            assert "Какую сумму просит пациент" in new_text
            assert core.draft["revision"] == revision and not core.stages
            assert "amount" not in core.draft["draftData"]["safetyScreening"]
            assert len(core.starts) == 1 and not core.accepts and not core.jobs

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["refresh", "edit", "manual", "menu"])
def test_failed_amount_operation_recovery_buttons_are_usable_when_discovery_omits_it(action):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            operation = await enter_amount(app, core, dispatch)
            fail_amount_operation(core, operation)
            await delivery_finished(app)
            before, revision = len(sent), core.draft["revision"]

            await dispatch(bound_callback(action, operation, DRAFT))

            new_text = "\n".join(message.get("text", "") for message in sent[before:])
            assert "Не удалось подтвердить результат действия" not in new_text, (
                "FAILED operations disappear from discovery, trapping every recovery callback"
            )
            assert core.draft["revision"] == revision and not core.stages
            assert "amount" not in core.draft["draftData"]["safetyScreening"]
            if action == "menu":
                assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            else:
                assert "Какую сумму просит пациент" in new_text
                assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SAFETY"
                if action == "manual":
                    await dispatch("text:10000")
                    assert core.draft["draftData"]["safetyScreening"]["amount"] == {
                        "amountKopecks": 1_000_000, "currency": "RUB",
                    }
                    assert len(core.starts) == 1, "explicit button fallback must bypass the model"

    asyncio.run(scenario())


def test_amount_staging_failure_before_commit_can_be_retried_without_losing_answer():
    async def scenario():
        async with simplified_harness() as (app, core, _sent, dispatch):
            operation = await enter_amount(app, core, dispatch)
            attempts = lose_stage_receipts(core, commit=False)
            AnswerCore.propose(core, {"amountKopecks": 1_000_000, "currency": "RUB"})
            await delivery_finished(app)
            assert len(attempts) == 2 and not core.stages
            assert "amount" not in core.draft["draftData"]["safetyScreening"]

            await dispatch(bound_callback("refresh", operation, DRAFT))

            assert len(core.starts) == 1 and list(core.stages) == [operation]
            assert core.draft["draftData"]["safetyScreening"]["amount"] == {
                "amountKopecks": 1_000_000, "currency": "RUB",
            }
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SUMMARY"

    asyncio.run(scenario())
