"""Real PTB dispatcher regression tests; only network boundaries are faked."""

import asyncio
from unittest.mock import patch
from uuid import UUID

import pytest
from telegram import Update
from telegram.ext import ExtBot
from telegram_gateway.quick_intake_runtime import build_application_with_quick_intake

ACTOR = 777
ESCALATION = UUID("00000000-0000-0000-0000-000000000321")
DRAFT = UUID("00000000-0000-0000-0000-000000000123")


class Core:
    def __init__(self):
        self.posts = []
        self.saved = []
        self.conclusions = []
        self.status = "REQUIRED"
        self.assigned = False

    async def get_actor(self, actor):
        return {"role": "CLINIC_OWNER"}

    async def get_escalation(self, escalation, actor):
        return {
            "escalationId": str(escalation),
            "publicNumber": "DL-TEST-1",
            "riskLevel": "HIGH",
            "status": self.status,
            "facts": {},
            "report": None,
            "assignedToMe": self.assigned,
            "assignedMembershipId": str(DRAFT) if self.assigned else None,
        }

    async def claim_escalation(self, escalation, actor):
        self.status = "IN_PROGRESS"
        self.assigned = True
        return await self.get_escalation(escalation, actor)

    async def resolve_escalation(self, escalation, actor, *, body):
        assert self.assigned and self.status == "IN_PROGRESS"
        self.conclusions.append(body)
        self.status = "RESOLVED"
        return await self.get_escalation(escalation, actor)

    async def list_case_escalations(self, actor, **kwargs):
        return {"items": [await self.get_escalation(ESCALATION, actor)], "nextBefore": None}

    async def get_escalation_discussion(self, escalation, actor, **kwargs):
        return {"items": [], "nextBefore": None}

    async def post_escalation_discussion_message(self, escalation, actor, *, body):
        self.posts.append(body)
        return {"body": body}

    async def create_intake_draft(self, actor):
        return {"id": str(DRAFT), "revision": 1, "wizardState": "INCIDENT"}

    async def get_intake_draft(self, draft, actor):
        return {
            "id": str(DRAFT),
            "revision": 1,
            "wizardState": "SERVICE_TYPE",
            "draftData": {"incident_type": "QUALITY_COMPLAINT"},
        }

    async def save_intake_draft(self, draft, actor, **kwargs):
        self.saved.append(kwargs)
        return {
            "id": str(DRAFT),
            "revision": kwargs["expected_revision"] + 1,
            "wizardState": kwargs["wizard_state"],
            "draftData": kwargs["draft_data"],
        }


@pytest.mark.parametrize(
    "flow,expected",
    [
        (
            [
                "case:start",
                "case:incident:QUALITY_COMPLAINT",
                f"case:escalation:{ESCALATION}",
                "text:Установка винира",
            ],
            "wizard",
        ),
        ([f"case:draft:{DRAFT}", "text:Установка винира"], "wizard"),
        (["quick:start", "text:Скололся винир, пациент требует вернуть деньги."], "quick"),
        (["menu", "text:Это не сообщение юристу"], "menu"),
        (
            [
                f"esc:claim:{ESCALATION}",
                "text:Уточните срок получения претензии.",
                f"esc:resolve:{ESCALATION}",
                "text:Уточнения получены, проверка завершена.",
                "esc:q:RESOLVED:first",
                f"case:escalation:{ESCALATION}",
                "text:Это уже не сообщение в завершённый кейс",
            ],
            "resolved",
        ),
        (
            [
                "quick:start",
                "case:start",
                "case:incident:QUALITY_COMPLAINT",
                "text:Установка винира",
            ],
            "wizard",
        ),
    ],
)
def test_switching_from_discussion_does_not_send_new_case_text_to_old_case(
    monkeypatch, flow, expected
):
    monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://agent-orchestrator:8010")
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "test-key-" * 8)

    async def scenario():
        sent = []

        async def fake_post(self, endpoint, data=None, **kwargs):
            if endpoint == "getMe":
                return {"id": 123, "is_bot": True, "first_name": "Test", "username": "test_bot"}
            if endpoint == "answerCallbackQuery":
                return True
            sent.append(data)
            return {
                "message_id": 10,
                "date": 1,
                "chat": {"id": ACTOR, "type": "private"},
                "text": (data or {}).get("text", ""),
            }

        with patch.object(ExtBot, "_post", fake_post):
            app = build_application_with_quick_intake("123456:unit_test_token_value_1234567890")
            core = Core()
            app.bot_data["legal_core_client"] = core
            errors = []

            async def record_error(update, context):
                errors.append(context.error)

            app.add_error_handler(record_error)
            await app.initialize()
            try:
                for i, content in enumerate([f"case:escalation:{ESCALATION}", *flow]):
                    message = {
                        "message_id": i + 1,
                        "date": 1,
                        "chat": {"id": ACTOR, "type": "private"},
                        "from": {"id": ACTOR, "is_bot": False, "first_name": "Tester"},
                    }
                    payload = {"update_id": i + 1}
                    if content.startswith("text:"):
                        message["text"] = content.removeprefix("text:")
                        payload["message"] = message
                    else:
                        payload["callback_query"] = {
                            "id": str(i),
                            "chat_instance": "test",
                            "from": message["from"],
                            "message": message,
                            "data": content,
                        }
                    await app.process_update(Update.de_json(payload, app.bot))
                assert errors == []
                if expected == "resolved":
                    assert core.posts == ["Уточните срок получения претензии."]
                    assert core.conclusions == ["Уточнения получены, проверка завершена."]
                    assert core.status == "RESOLVED"
                    assert "escalation_discussion_id" not in app.user_data[ACTOR]
                else:
                    assert core.posts == [], "new case facts were leaked into the old escalation"
                if expected == "wizard":
                    assert core.saved, sent
                    assert core.saved[-1]["draft_data"]["service_type"] == "Установка винира"
                elif expected == "quick":
                    assert "quick_intake_candidate" in app.user_data[ACTOR], sent
                else:
                    assert "escalation_discussion_id" not in app.user_data[ACTOR]
            finally:
                await app.shutdown()

    asyncio.run(scenario())
