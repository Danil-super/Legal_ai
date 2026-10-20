"""Independent review: a transient status read must not silently strand intake."""

import asyncio
from unittest.mock import patch

import pytest
from telegram_gateway import bot, intake_delivery
from telegram_gateway.case_wizard import LegalCoreApiError
from test_answer_runtime import AnswerCore
from test_confirmed_understanding_runtime import TEXT
from test_dialog_isolation import ACTOR, DRAFT
from test_intake_single_summary import simplified_harness


@pytest.mark.parametrize("initial", [True, False], ids=["quick", "followup"])
def test_one_transient_status_read_is_retried_or_user_gets_clear_recovery(initial):
    async def scenario():
        async with simplified_harness() as (app, core, sent, dispatch):
            await dispatch("quick:start" if initial else f"case:draft:{DRAFT}")
            request = core._json_request
            reads = 0

            async def fail_first_status_read(method, path, **kwargs):
                nonlocal reads
                if method == "GET" and path.rsplit("/", 1)[-1] not in {
                    "understanding", "answer-interpretations",
                } and "/intake-" in path:
                    reads += 1
                    if reads == 1:
                        raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "synthetic")
                return await request(method, path, **kwargs)

            core._json_request = fail_first_status_read
            with patch.object(intake_delivery, "POLL_INTERVAL", 0.001):
                await dispatch("text:" + TEXT)
                before_error = len(sent)
                if initial:
                    core.propose(incomingKind="COMPLAINT")
                else:
                    AnswerCore.propose(core, "COMPLAINT")
                await asyncio.sleep(0.05)

            advanced = app.user_data[ACTOR].get(bot.WIZARD_DATA_KEY, {}).get(
                bot.DRAFT_STATE_KEY,
            ) == "SITUATION"
            clear_recovery = any("повтор" in item.get("text", "").lower()
                                 or "недоступ" in item.get("text", "").lower()
                                 for item in sent[before_error:])
            assert advanced or clear_recovery, (
                f"{reads=}, no automatic retry, no failure notice; only cancel remains visible"
            )

    asyncio.run(scenario())
