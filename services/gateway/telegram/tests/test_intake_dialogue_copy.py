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


def test_optional_file_step_explains_what_to_attach_and_why():
    from types import SimpleNamespace

    from telegram_gateway import bot

    async def scenario():
        sent = []

        async def reply_text(text, **kwargs):
            sent.append((text, kwargs))
            return SimpleNamespace(message_id=11)

        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text))
        await bot._prompt_v2_draft(update, bot.WizardState.MATERIALS, {"intakeVersion": 2})
        prompt, kwargs = sent[-1]
        assert "претензи" in prompt.casefold() and "переписк" in prompt.casefold()
        assert "юрист" in prompt.casefold() and "без файла" in prompt.casefold()
        assert "15" in prompt and ".pdf" in prompt and "ФИО" in prompt
        assert "Hermes" not in prompt and "рекомендации на основе файла" not in prompt
        assert any("без файла" in button.text.casefold()
                   for row in kwargs["reply_markup"].inline_keyboard for button in row)

    asyncio.run(scenario())


def test_event_date_question_names_the_appeal_not_the_service():
    from types import SimpleNamespace

    from telegram_gateway import bot

    async def scenario():
        sent = []

        async def reply_text(text, **kwargs):
            sent.append(text)
            return SimpleNamespace(message_id=12)

        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text))
        await bot._prompt_v2_draft(update, bot.WizardState.EVENT_DATE, {"intakeVersion": 2})
        assert "пациент" in sent[-1].casefold() and "обратил" in sent[-1].casefold()
        assert "Когда это произошло" not in sent[-1]
        assert "не знаете" in sent[-1]

    asyncio.run(scenario())
