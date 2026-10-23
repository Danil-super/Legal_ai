"""Real PTB dispatcher regression tests; only network boundaries are faked."""

import asyncio
from unittest.mock import patch
from uuid import UUID

import pytest
from telegram import Update
from telegram.ext import ExtBot
from telegram_gateway.analysis_jobs_runtime import JOBS_CLIENT_KEY
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
        self.jobs = []

    async def get_actor(self, actor):
        return {"role": "CLINIC_OWNER"}

    async def enqueue(self, case, actor, message_id):
        self.jobs.append((case, actor, message_id))
        return {
            "jobId": str(DRAFT),
            "caseId": str(case),
            "state": "QUEUED",
            "result": None,
            "errorCode": None,
        }

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

    async def create_intake_draft(self, actor, *, intake_schema_version):
        assert intake_schema_version == "dental-case-intake.v2"
        self.draft = {
            "id": str(DRAFT),
            "revision": 1,
            "wizardState": "INCOMING",
            "draftData": {"intakeVersion": 2},
        }
        return self.draft

    async def get_intake_draft(self, draft, actor):
        if hasattr(self, "draft"):
            return self.draft
        return {
            "id": str(DRAFT),
            "revision": 1,
            "wizardState": "SERVICE_TYPE",
            "draftData": {"incident_type": "QUALITY_COMPLAINT"},
        }

    async def save_intake_draft(self, draft, actor, **kwargs):
        self.saved.append(kwargs)
        self.draft = {
            "id": str(DRAFT),
            "revision": kwargs["expected_revision"] + 1,
            "wizardState": kwargs["wizard_state"],
            "draftData": kwargs["draft_data"],
        }
        return self.draft


@pytest.mark.parametrize(
    "flow,expected",
    [
        (
            [
                "case:start",
                    "case:v2:single:incomingKind:MESSAGE_OR_REQUEST",
                "case:v2:multi:situationAreas:TREATMENT",
                "case:v2:multi:situationAreas:DONE",
                f"case:escalation:{ESCALATION}",
                "text:Установка винира",
            ],
            "wizard",
        ),
        ([f"case:draft:{DRAFT}", "text:Установка винира"], "wizard"),
        (["quick:start", "text:Скололся винир, пациент требует вернуть деньги."], "quick"),
        (["menu", "text:Это не сообщение юристу"], "menu"),
        ([f"case:analyze:{DRAFT}", "menu", "text:Проверка доступности меню"], "analysis"),
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
                "case:v2:single:incomingKind:MESSAGE_OR_REQUEST",
                "case:v2:multi:situationAreas:TREATMENT",
                "case:v2:multi:situationAreas:DONE",
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
                "message_id": len(sent) + 100,
                "date": 1,
                "chat": {"id": ACTOR, "type": "private"},
                "text": (data or {}).get("text", ""),
            }

        with patch.object(ExtBot, "_post", fake_post):
            app = build_application_with_quick_intake("123456:unit_test_token_value_1234567890")
            core = Core()
            app.bot_data["legal_core_client"] = core
            app.bot_data[JOBS_CLIENT_KEY] = core
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
                        for index in range(len(sent) - 1, -1, -1):
                            markup = sent[index].get("reply_markup")
                            if markup is not None and any(
                                button.callback_data == content
                                for row in markup.inline_keyboard for button in row
                            ):
                                message["message_id"] = index + 101
                                break
                        payload["callback_query"] = {
                            "id": str(i),
                            "chat_instance": "test",
                            "from": message["from"],
                            "message": message,
                            "data": content,
                        }
                    await asyncio.wait_for(app.process_update(Update.de_json(payload, app.bot)), 1)
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
                    saved_data = core.saved[-1]["draft_data"]
                    assert saved_data.get("affectedServices") == ["Установка винира"] or (
                        saved_data.get("service_type") == "Установка винира"
                    )
                elif expected == "quick":
                    assert "quick_intake_candidate" in app.user_data[ACTOR], sent
                elif expected == "analysis":
                    queued_message_id = next(
                        index + 101 for index, item in enumerate(sent)
                        if item.get("text") == "⏳ Сохраняю запрос анализа…"
                    )
                    assert core.jobs == [(DRAFT, ACTOR, queued_message_id)]
                    assert "escalation_discussion_id" not in app.user_data[ACTOR]
                    assert "меню" in sent[-1]["text"].lower() or "/menu" in sent[-1]["text"]
                else:
                    assert "escalation_discussion_id" not in app.user_data[ACTOR]
            finally:
                await app.shutdown()

    asyncio.run(scenario())


def test_owner_transfer_reaches_lawyer_conclusion_through_real_dispatcher(monkeypatch):
    """Exercise actual callbacks and message routing, with Telegram/Core network fakes."""
    monkeypatch.setenv("AGENT_ORCHESTRATOR_URL", "http://agent-orchestrator:8010")
    monkeypatch.setenv("AGENT_INTERNAL_KEY", "test-key-" * 8)
    lawyer = ACTOR + 1

    class AssignmentCore(Core):
        def __init__(self):
            super().__init__()
            self.assignee = ACTOR
            self.status = "IN_PROGRESS"
            self.transfers = []

        async def get_actor(self, actor):
            return {"role": "CLINIC_OWNER" if actor == ACTOR else "CLINIC_LAWYER"}

        async def get_escalation(self, escalation, actor):
            return {**await super().get_escalation(escalation, actor),
                    "assignedToMe": actor == self.assignee,
                    "assignedMembershipId": str(DRAFT if self.assignee == ACTOR else ESCALATION),
                    "assignedRole": (await self.get_actor(self.assignee))["role"]}

        async def list_clinic_members(self, actor):
            assert actor == ACTOR
            return {"items": [{"membershipId": str(ESCALATION), "telegramUserId": lawyer,
                               "role": "CLINIC_LAWYER"}]}

        async def assign_escalation(self, escalation, actor, **payload):
            assert actor == ACTOR and escalation == ESCALATION
            assert payload == {"assigned_membership_id": ESCALATION,
                               "expected_assigned_membership_id": DRAFT}
            self.transfers.append(payload)
            self.assignee = lawyer
            self.assigned = True
            return await self.get_escalation(escalation, actor)

    async def scenario():
        sent, errors = [], []

        async def fake_post(self, endpoint, data=None, **kwargs):
            if endpoint == "getMe":
                return {"id": 123, "is_bot": True, "first_name": "Test", "username": "test_bot"}
            if endpoint == "answerCallbackQuery":
                return True
            sent.append(data)
            return {"message_id": len(sent) + 100, "date": 1,
                    "chat": {"id": data["chat_id"], "type": "private"},
                    "text": data.get("text", "")}

        def callback(prefix):
            for item in reversed(sent):
                markup = item.get("reply_markup")
                if markup:
                    for row in markup.inline_keyboard:
                        for button in row:
                            if str(button.callback_data).startswith(prefix):
                                return button.callback_data
            raise AssertionError(prefix)

        with patch.object(ExtBot, "_post", fake_post):
            app = build_application_with_quick_intake("123456:unit_test_token_value_1234567890")
            core = AssignmentCore()
            app.bot_data["legal_core_client"] = core

            async def record_error(update, context):
                errors.append(context.error)

            async def dispatch(actor, content):
                sequence = len(sent) + 1
                user = {"id": actor, "is_bot": False, "first_name": "Tester"}
                message = {"message_id": sequence, "date": 1,
                           "chat": {"id": actor, "type": "private"}, "from": user}
                payload = {"update_id": sequence}
                if content.startswith("text:"):
                    message["text"] = content.removeprefix("text:")
                    payload["message"] = message
                else:
                    payload["callback_query"] = {"id": str(sequence), "chat_instance": "test",
                                                 "from": user, "message": message, "data": content}
                await asyncio.wait_for(app.process_update(Update.de_json(payload, app.bot)), 5)

            app.add_error_handler(record_error)
            await app.initialize()
            try:
                await dispatch(ACTOR, f"case:escalation:{ESCALATION}")
                await dispatch(ACTOR, callback("esc:assign:"))
                await dispatch(ACTOR, callback("esc:pick:"))
                assert core.transfers == []
                await dispatch(ACTOR, callback("esc:confirm-assign:"))
                assert len(core.transfers) == 1
                await dispatch(lawyer, f"case:escalation:{ESCALATION}")
                await dispatch(lawyer, "text:Уточните дату получения требования.")
                await dispatch(lawyer, callback("esc:resolve:"))
                await dispatch(lawyer, "text:Проверка завершена. Рекомендации направлены клинике.")
                assert errors == []
                assert core.posts == ["Уточните дату получения требования."]
                assert core.conclusions == ["Проверка завершена. Рекомендации направлены клинике."]
                assert core.status == "RESOLVED"
            finally:
                await app.shutdown()

    asyncio.run(scenario())
