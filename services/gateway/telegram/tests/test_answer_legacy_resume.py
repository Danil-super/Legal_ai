"""Full factual-answer opt-in must leave owned legacy drafts on their original wizard."""

import asyncio

import pytest
from telegram_gateway import bot
from test_answer_http_postgres_flow import full_harness
from test_case_experience import ExperienceCore
from test_dialog_isolation import ACTOR, DRAFT


class LegacyCore(ExperienceCore):
    def __init__(self):
        super().__init__()
        self.discovery_calls = 0
        self.draft.update(wizardState="SERVICE_TYPE",
                          draftData={"incident_type": "QUALITY_COMPLAINT"})

    async def _json_request(self, method, path, *, telegram_user_id, **kwargs):
        if method == "GET" and path.endswith(("/understanding", "/answer-interpretations")):
            self.discovery_calls += 1
            return {"operation": None}
        return await super()._json_request(method, path, telegram_user_id=telegram_user_id,
                                           **kwargs)


def test_full_opt_in_resumes_owned_legacy_question_without_entering_v2_discovery():
    async def scenario():
        async with full_harness() as (app, _core, _sent, dispatch):
            core = LegacyCore()
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = core
            await dispatch(f"case:draft:{DRAFT}")
            assert app.user_data[ACTOR][bot.WIZARD_DATA_KEY][bot.DRAFT_STATE_KEY] == "SERVICE_TYPE"
            assert core.discovery_calls == 0
            assert not core.saved and not core.jobs
    asyncio.run(scenario())


@pytest.mark.parametrize("version", [None, True, 1, 3, "2"])
def test_present_invalid_v2_version_never_falls_back_to_legacy_resume(version):
    async def scenario():
        async with full_harness() as (app, _core, _sent, dispatch):
            core = LegacyCore()
            core.draft["draftData"]["intakeVersion"] = version
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = core
            await dispatch(f"case:draft:{DRAFT}")
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            assert not core.saved and not core.jobs
    asyncio.run(scenario())
