"""The starting instruction should invite a situation, not demand a legal questionnaire."""

import asyncio

from test_case_experience import harness


def test_quick_entry_invites_plain_description_with_a_short_privacy_notice():
    async def scenario():
        async with harness() as (_app, _core, sent, dispatch):
            await dispatch("quick:start")
            prompt = sent[-1]["text"]
            assert len(prompt) < 400
            assert "ФИО" in prompt and "1500" in prompt
            assert "уточн" in prompt.casefold()
            assert "юридический вывод" not in prompt.casefold()
    asyncio.run(scenario())
