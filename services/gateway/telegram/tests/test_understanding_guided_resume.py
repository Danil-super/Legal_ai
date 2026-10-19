"""Initial narrative understanding must not prevent resuming an ordinary guided draft."""

import asyncio

import pytest
from telegram_gateway import bot
from telegram_gateway.case_wizard import LegalCoreApiError
from test_confirmed_understanding_runtime import UnderstandingCore, active_harness
from test_dialog_isolation import ACTOR, DRAFT


class GuidedCore(UnderstandingCore):
    def __init__(self, data):
        super().__init__()
        self.draft.update(wizardState="INCOMING", draftData=data)
        self.discovery_calls = 0

    async def _json_request(self, method, path, *, telegram_user_id, **kwargs):
        if path.endswith("/understanding") and method == "GET":
            self.discovery_calls += 1
            # The real initial-discovery contract requires a valid narrative;
            # absent or invalid source is not a model operation to recover.
            raise LegalCoreApiError(422, "UNDERSTANDING_SOURCE_INVALID", "Unavailable")
        return await super()._json_request(method, path, telegram_user_id=telegram_user_id,
                                           **kwargs)


def test_guided_resume_without_narrative_opens_saved_question_without_initial_model_discovery():
    async def scenario():
        async with active_harness() as (app, _core, _sent, dispatch):
            core = GuidedCore({"intakeVersion": 2})
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = core
            await dispatch(f"case:draft:{DRAFT}")
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "INCOMING"
            assert core.discovery_calls == 0 and not core.starts and not core.saved
    asyncio.run(scenario())


@pytest.mark.parametrize("source", [None, "short", "synthetic@example.invalid"])
def test_present_invalid_narrative_still_uses_fail_closed_initial_boundary(source):
    async def scenario():
        async with active_harness() as (app, _core, sent, dispatch):
            core = GuidedCore({"intakeVersion": 2, "eventSummary": source})
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = core
            await dispatch(f"case:draft:{DRAFT}")
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            assert not core.starts and not core.saved
            if source:
                assert all(source not in item.get("text", "") for item in sent)
    asyncio.run(scenario())
