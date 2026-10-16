"""Independent strict-identity probes for the full opt-in legacy escape hatch."""

import asyncio
from uuid import uuid4

import pytest
from telegram_gateway import bot
from test_answer_http_postgres_flow import full_harness
from test_answer_legacy_resume import LegacyCore
from test_dialog_isolation import ACTOR, DRAFT


@pytest.mark.parametrize("changed", [
    {"id": str(uuid4())}, {"revision": True}, {"revision": 0},
    {"wizardState": "INCOMING"}, {"wizardState": "UNKNOWN_STATE"},
])
def test_independent_legacy_resume_requires_exact_identity_revision_and_legacy_state(changed):
    async def scenario():
        async with full_harness() as (app, _, _, dispatch):
            core = LegacyCore()
            core.draft.update(changed)
            app.bot_data[bot.LEGAL_CORE_CLIENT_KEY] = core
            await dispatch(f"case:draft:{DRAFT}")
            assert bot.WIZARD_DATA_KEY not in app.user_data[ACTOR]
            assert core.discovery_calls == 0
            assert not core.saved and not core.jobs

    asyncio.run(scenario())
