import asyncio
from types import SimpleNamespace
from uuid import UUID

from telegram_gateway.escalation_workspace import (
    discussion_messages,
    history_callback,
    parse_history_callback,
    render_case_card,
    workspace_keyboard,
)

ESCALATION = UUID("00000000-0000-0000-0000-000000000321")
CURSOR = UUID("00000000-0000-0000-0000-000000000322")


def test_history_preserves_entire_latest_messages_and_has_bounded_cursor():
    entries = [
        {
            "authorRole": "CLINIC_LAWYER",
            "createdAt": "2026-09-12T12:00:00Z",
            "body": str(i) + "x" * 1490,
        }
        for i in range(5)
    ]
    chunks = discussion_messages({"items": entries})
    joined = "\n".join(chunks)
    assert all(len(chunk) <= 3900 for chunk in chunks)
    assert all(entry["body"] in joined for entry in entries)
    callback = history_callback(ESCALATION, CURSOR)
    assert len(callback.encode()) <= 64
    assert parse_history_callback(callback) == (ESCALATION, CURSOR)


def test_case_card_and_actions_distinguish_owner_and_resolved_case():
    detail = {
        "publicNumber": "DL-TEST-1",
        "riskLevel": "CRITICAL",
        "reasonCodes": ["HOSPITALIZATION_REPORTED"],
        "status": "IN_PROGRESS",
        "assignedToMe": True,
        "assignedMembershipId": str(CURSOR),
        "facts": {"PROBLEM_SUMMARY": {"text": "Обезличенное описание"}},
        "report": None,
    }
    card = render_case_card(detail)
    assert "Госпитализация" in card
    assert "Обезличенное описание" in card
    assert "не сформирован" in card
    buttons = workspace_keyboard(ESCALATION, detail, can_manage=True).inline_keyboard
    assert any("Завершить" in b.text for row in buttons for b in row)
    detail["status"] = "RESOLVED"
    buttons = workspace_keyboard(ESCALATION, detail, can_manage=True).inline_keyboard
    assert not any("Завершить" in b.text for row in buttons for b in row)


def test_v2_case_card_explains_facts_without_json_or_internal_codes():
    facts = {
        "INTAKE_VERSION": "GUIDED_V2",
        "EVENT_SUMMARY": "Учебный пример: пациент просит пояснить стоимость консультации.",
        "INCOMING_COMMUNICATION": "MESSAGE_OR_REQUEST",
        "SITUATION_AREAS": ["TREATMENT"],
        "AFFECTED_SERVICES": ["консультация"],
        "EVENT_DATE": {"date": "2026-09-12", "precision": "EXACT"},
        "CLINIC_ACTIONS": ["INVITED_FOR_EXAMINATION"],
        "HEALTH_CONSEQUENCE_SIGNALS": ["UNKNOWN"],
        "FACTUAL_SAFETY_SCREENING": {
            "schemaVersion": "factual-safety-intake.v1",
            "hospitalizationReported": "NO", "healthDeteriorationReported": "UNKNOWN",
            "moneyRequested": "YES", "amount": {"amountKopecks": 1_000_050, "currency": "RUB"},
        },
        "ANALYSIS_CLARIFICATION_ANSWERS": {"answers": [{
            "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
            "value": {"date": "2026-08-20", "precision": "APPROXIMATE"},
        }]},
    }
    card = render_case_card({
        "publicNumber": "DL-TEST-2", "riskLevel": "HIGH", "status": "REQUIRED",
        "reasonCodes": ["HIGH_DEMAND_AMOUNT"], "facts": facts, "report": None,
    })
    assert "стоимость консультации" in card and "2026-09-12" in card
    assert "на осмотр" in card.casefold()
    assert "2026-08-20" in card and "примерно" in card
    assert "10000 \u0440\u0443\u0431. 50 \u043a\u043e\u043f." in card
    assert "неизвестно" in card and "госпитализации: нет" in card.casefold()
    for code in ("EVENT_SUMMARY", "INVITED_FOR_EXAMINATION", "GUIDED_V2",
                 "SERVICE_PROVIDED_DATE", "schemaVersion", "Legal Core"):
        assert code not in card
    assert "{" not in card and "[" not in card


def test_unknown_case_fact_format_is_visible_without_dumping_internal_payload():
    card = render_case_card({
        "facts": {"NEW_INTERNAL_FACT": {"hidden": "INTERNAL_VALUE"}},
        "reasonCodes": ["NEW_INTERNAL_REASON"],
    })
    assert "сведен" in card.casefold()
    assert "NEW_INTERNAL_FACT" not in card and "INTERNAL_VALUE" not in card
    assert "NEW_INTERNAL_REASON" not in card and "{" not in card


def test_resolution_requires_explicit_conclusion_and_is_not_posted_as_normal_reply():
    import pytest
    from telegram.ext import ApplicationHandlerStop
    from telegram_gateway.analysis_runtime import post_escalation_discussion_message

    class Core:
        def __init__(self):
            self.conclusions = []

        async def resolve_escalation(self, escalation, actor, *, body):
            self.conclusions.append(body)
            return {"status": "RESOLVED"}

    async def scenario():
        core = Core()
        replies = []

        async def reply(text, **kwargs):
            replies.append(text)

        context = SimpleNamespace(
            user_data={
                "escalation_discussion_id": str(ESCALATION),
                "escalation_resolution_pending": str(ESCALATION),
            },
            bot_data={"legal_core_client": core},
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=777),
            effective_message=SimpleNamespace(text="Проверка завершена.", reply_text=reply),
        )
        with pytest.raises(ApplicationHandlerStop):
            await post_escalation_discussion_message(update, context)
        assert core.conclusions == ["Проверка завершена."]
        assert "escalation_discussion_id" not in context.user_data
        assert "escalation_resolution_pending" not in context.user_data
        assert any("заверш" in text.lower() for text in replies)

    asyncio.run(scenario())
