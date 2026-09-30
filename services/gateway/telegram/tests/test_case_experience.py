# ruff: noqa: RUF001
"""Actual production PTB composition with mocked Telegram and Core boundaries."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import patch
from uuid import UUID

import pytest
from telegram import Update
from telegram.ext import ExtBot
from telegram_gateway import bot as gateway_bot
from telegram_gateway.case_experience_runtime import build_application_with_case_experience
from telegram_gateway.case_wizard import LegalCoreApiError
from telegram_gateway.intake_experience import (
    FIELDS,
    active_states,
    confirmed_candidates,
    drop_field,
    next_missing_state,
    parse_answer,
    valid_value,
    value_label,
)
from telegram_gateway.update_processor import ActorSerialUpdateProcessor
from test_dialog_isolation import ACTOR, DRAFT, ESCALATION, Core

DESCRIPTION = (
    "После установки коронки появился скол. Пациент требует вернуть 35 000 рублей. "
    "Письменной претензии нет. Все основные документы есть."
)


def complete_data():
    return {
        "incident_type": "QUALITY_COMPLAINT", "service_type": "установка коронки",
        "service_date": {"date": "2026-06-01", "precision": "EXACT"},
        "incident_date": {"date": "2026-07-01", "precision": "EXACT"},
        "claim_date": {"date": "2026-07-02", "precision": "EXACT"},
        "problem_summary": "Пациент сообщил о сколе коронки.", "patient_demand": "REWORK_DEMAND",
        "formal_claim": "NO", "harm_claimed": "NO", "lawyer_contact": "NO",
        "regulator_or_court": "NO", "regulator_threat": "NO", "documents_status": "COMPLETE",
    }


@pytest.mark.parametrize("state", [
    "INCIDENT", "SERVICE_TYPE", "SERVICE_DATE", "INCIDENT_DATE", "CLAIM_DATE",
    "PROBLEM_SUMMARY", "PATIENT_DEMAND", "FORMAL_CLAIM", "HARM", "LAWYER",
    "AUTHORITY", "REGULATOR_THREAT", "DOCUMENTS",
])
def test_only_missing_confirmed_field_is_requested(state):
    data = complete_data()
    assert next_missing_state(data) == "CONFIRM"
    data.pop(FIELDS[state][0])
    assert next_missing_state(data) == state


@pytest.mark.parametrize("field,value,expected", [
    ("patient_demand", "REFUND_DEMAND", "DEMAND_AMOUNT"),
    ("formal_claim", "YES", "CLAIM_RECEIVED_AT"),
    ("harm_claimed", "YES", "HOSPITALIZATION"),
    ("harm_claimed", "UNKNOWN", "HOSPITALIZATION"),
    ("lawyer_contact", "YES", "REPRESENTATIVE_AUTHORITY"),
    ("regulator_or_court", "YES", "AUTHORITY_KIND"),
])
def test_conditional_questions_are_not_skipped(field, value, expected):
    data = complete_data()
    data[field] = value
    assert next_missing_state(data) == expected


def test_sparse_proposal_preserves_later_fields_without_making_absence_no():
    data = confirmed_candidates(DESCRIPTION)
    assert "service_date" not in data
    assert data["demand_amount_kopecks"] == 3_500_000
    assert data["patient_demand"] == "REFUND_DEMAND"
    assert data["problem_summary"] == DESCRIPTION
    assert data["formal_claim"] == "NO"
    assert "harm_claimed" not in data and "lawyer_contact" not in data
    assert next_missing_state(data) == "SERVICE_DATE"


@pytest.mark.parametrize("description", [
    "Пациент не госпитализирован, жалуется на боль после лечения.",
    "Пациент госпитализирован, сообщил о вреде здоровью.",
])
def test_hospitalization_requires_a_human_answer_not_substring_match(description):
    assert "hospitalization" not in confirmed_candidates(description)


def test_uncertain_or_conflicting_extraction_is_not_silently_prefilled():
    data = confirmed_candidates(
        "Примерно 01.06.2026 установили коронку. Пациент просит переделать."
    )
    assert "service_date" not in data
    data = confirmed_candidates("Письменной претензии нет. Позже получили претензию о лечении.")
    assert "formal_claim" not in data


def test_unknown_dates_remain_unknown_and_input_formats_work():
    assert parse_answer("service_date", "01.06.2026") == {
        "date": "2026-06-01", "precision": "EXACT",
    }
    data = complete_data()
    data["claim_date"] = parse_answer("claim_date", "неизвестно")
    assert data["claim_date"] == {"date": None, "precision": "UNKNOWN"}
    assert next_missing_state(data) == "CONFIRM"  # completeness is NOT legal date applicability
    assert "Неизвестно" in value_label("claim_date", data["claim_date"])
    assert "HOSPITALIZATION" not in active_states(data)


def test_editing_parent_invalidates_dependents_not_unrelated_answers():
    data = complete_data()
    data.update(formal_claim="YES", claim_received_at="2026-07-02", response_deadline="2026-07-12")
    drop_field(data, "formal_claim")
    assert "response_deadline" not in data and "claim_received_at" not in data
    assert data["service_type"] == "установка коронки"
    assert next_missing_state(data) == "FORMAL_CLAIM"


@pytest.mark.parametrize("value,label", [(3_500_000, "35 000,00 ₽"),
                                        (123_456_789, "1 234 567,89 ₽"), (1, "0,01 ₽")])
def test_money_presentation_is_integer_based(value, label):
    assert value_label("demand_amount_kopecks", value) == label


@pytest.mark.parametrize("value", [True, -1, "100", None])
def test_invalid_money_is_not_confirmed(value):
    assert not valid_value("demand_amount_kopecks", value)


class ExperienceCore(Core):
    def __init__(self):
        super().__init__()
        self.draft = {"id": str(DRAFT), "revision": 1, "wizardState": "INCIDENT", "draftData": {}}
        self.requests = []
        self.replays = {}
        self.fail_once = False
        self.deny = False

    async def _json_request(self, method, path, *, telegram_user_id, idempotency_key, payload):
        self.requests.append((method, path, telegram_user_id, idempotency_key))
        if self.deny:
            raise LegalCoreApiError(403, "ACTOR_NOT_AUTHORIZED", "denied")
        key = (method, path, idempotency_key)
        if key in self.replays:
            return deepcopy(self.replays[key])
        if method == "POST":
            result = deepcopy(self.draft)
        else:
            result = await self.save_intake_draft(DRAFT, telegram_user_id,
                expected_revision=payload["expectedRevision"], wizard_state=payload["wizardState"],
                draft_data=payload["draftData"])
        self.replays[key] = deepcopy(result)
        if method == "PUT" and self.fail_once:
            self.fail_once = False
            raise LegalCoreApiError(503, "LEGAL_CORE_UNAVAILABLE", "ambiguous save")
        return result

    async def get_intake_draft(self, draft, actor):
        return deepcopy(self.draft)

    async def save_intake_draft(self, draft, actor, **kwargs):
        result = await super().save_intake_draft(draft, actor, **kwargs)
        self.draft = deepcopy(result)
        return result


@asynccontextmanager
async def harness():
    sent = []

    async def fake_post(self, endpoint, data=None, **kwargs):
        if endpoint == "getMe":
            return {"id": 123, "is_bot": True, "first_name": "Test", "username": "test_bot"}
        if endpoint == "answerCallbackQuery":
            return True
        data = dict(data or {})
        sent.append(data)
        return {"message_id": len(sent) + 100, "date": 1,
                "chat": {"id": data.get("chat_id", ACTOR), "type": "private"},
                "text": data.get("text", "")}

    with patch.object(ExtBot, "_post", fake_post):
        app = build_application_with_case_experience("123456:unit_test_token_value_1234567890")
        core = ExperienceCore()
        app.bot_data["legal_core_client"] = core
        errors = []

        async def record_error(update, context):
            errors.append(type(context.error).__name__)

        app.add_error_handler(record_error)
        await app.initialize()
        counter = 0

        async def dispatch(content, actor=ACTOR, chat_type="private"):
            nonlocal counter
            counter += 1
            user = {"id": actor, "is_bot": False, "first_name": "Synthetic"}
            message = {"message_id": counter, "date": 1, "from": user,
                       "chat": {"id": actor, "type": chat_type}}
            body = {"update_id": counter}
            if content.startswith("text:") or content.startswith("/"):
                message["text"] = content.removeprefix("text:")
                if content.startswith("/"):
                    message["entities"] = [{"type": "bot_command", "offset": 0,
                                            "length": len(content)}]
                body["message"] = message
            else:
                body["callback_query"] = {"id": str(counter), "chat_instance": "synthetic",
                                          "from": user, "message": message, "data": content}
            await asyncio.wait_for(app.process_update(Update.de_json(body, app.bot)), 2)
            assert not errors, errors

        try:
            yield app, core, sent, dispatch
        finally:
            await app.shutdown()


def test_production_flow_persists_all_confirmed_fields_then_skips_repetition():
    async def scenario():
        async with harness() as (app, core, sent, dispatch):
            assert isinstance(app.update_processor, ActorSerialUpdateProcessor)
            await dispatch("quick:start")
            await dispatch("text:" + DESCRIPTION)
            assert core.saved == []
            pending = app.user_data[ACTOR]["quick_intake_candidate"]
            accept = f"quick:review:{pending['nonce']}:accept"
            await dispatch(accept)
            assert core.draft["draftData"]["demand_amount_kopecks"] == 3_500_000
            await dispatch(accept)
            assert len(core.saved) == 1  # stale confirmation cannot create another draft
            await dispatch(f"case:draft:{DRAFT}")
            for value in ("01.06.2026", "01.07.2026", "02.07.2026"):
                await dispatch("text:" + value)
            assert core.draft["wizardState"] == "HARM"
            assert core.draft["draftData"]["problem_summary"] == DESCRIPTION
            assert core.draft["draftData"]["patient_demand"] == "REFUND_DEMAND"
            for callback in ("case:harm:no", "case:lawyer:no", "case:authority:no",
                             "case:regulator_threat:no"):
                await dispatch(callback)
            assert core.draft["wizardState"] == "CONFIRM"
            assert any("Проверьте карточку" in message.get("text", "") for message in sent)

    asyncio.run(scenario())


def test_correct_only_selected_field_and_reject_stale_card():
    async def scenario():
        async with harness() as (app, core, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + DESCRIPTION)
            pending = app.user_data[ACTOR]["quick_intake_candidate"]
            old = pending["nonce"]
            await dispatch(f"quick:review:{old}:drop:patient_demand")
            assert "patient_demand" not in pending["candidate_data"]
            assert "demand_amount_kopecks" not in pending["candidate_data"]
            assert pending["candidate_data"]["problem_summary"] == DESCRIPTION
            await dispatch(f"quick:review:{old}:accept")
            assert not core.requests
            await dispatch(f"quick:review:{pending['nonce']}:accept", actor=ACTOR + 1)
            assert not core.requests
            await dispatch(f"quick:review:{pending['nonce']}:accept")
            assert len(core.saved) == 1

    asyncio.run(scenario())


def test_ambiguous_save_retry_keeps_idempotency_keys():
    async def scenario():
        async with harness() as (app, core, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + DESCRIPTION)
            pending = app.user_data[ACTOR]["quick_intake_candidate"]
            accept = f"quick:review:{pending['nonce']}:accept"
            core.fail_once = True
            await dispatch(accept)
            assert "quick_intake_candidate" in app.user_data[ACTOR]
            await dispatch(accept)
            assert len(core.saved) == 1
            assert core.requests[0] == core.requests[2]
            assert core.requests[1] == core.requests[3]

    asyncio.run(scenario())


def test_revoked_access_and_group_commands_do_not_save():
    async def scenario():
        async with harness() as (app, core, _sent, dispatch):
            await dispatch("quick:start")
            await dispatch("text:" + DESCRIPTION)
            pending = app.user_data[ACTOR]["quick_intake_candidate"]
            accept = f"quick:review:{pending['nonce']}:accept"
            await dispatch(accept, chat_type="group")
            assert not core.requests
            core.deny = True
            await dispatch(accept)
            assert not core.saved

    asyncio.run(scenario())


def test_plan_and_specialist_note_require_preview_confirmation_and_keep_history():
    async def scenario():
        async with harness() as (app, core, _sent, dispatch):
            await dispatch(f"esc:claim:{ESCALATION}")
            for kind in ("plan", "review"):
                await dispatch(f"esc:{kind}:{ESCALATION}")
                await dispatch("text:Уточнить документы; внутренний срок — завтра.")
                before = len(core.posts)
                pending = app.user_data[ACTOR]["escalation_resolution_pending"]
                save = f"esc:note-save:{pending['nonce']}"
                await dispatch(save)
                await dispatch(save)
                assert len(core.posts) == before + 1
                assert core.status == "IN_PROGRESS" and not core.conclusions
            assert "РАБОЧИЙ ПЛАН" in core.posts[0]
            assert "не меняет автоматическую оценку риска" in core.posts[1]

    asyncio.run(scenario())


def test_closure_requires_confirmation_and_blocks_new_discussion_afterwards():
    async def scenario():
        async with harness() as (app, core, _sent, dispatch):
            await dispatch(f"esc:claim:{ESCALATION}")
            await dispatch(f"esc:resolve:{ESCALATION}")
            await dispatch("text:Проверка закончена. Причина: информирование. Улучшить инструкцию.")
            assert core.status == "IN_PROGRESS" and not core.conclusions
            pending = app.user_data[ACTOR]["escalation_resolution_pending"]
            save = f"esc:note-save:{pending['nonce']}"
            await dispatch(save)
            await dispatch(save)
            assert len(core.conclusions) == 1 and core.status == "RESOLVED"
            await dispatch("text:Не отправлять в закрытое дело")
            assert not core.posts

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["assignment", "menu", "another_case"])
def test_pending_note_cannot_write_after_context_or_assignment_change(change):
    async def scenario():
        async with harness() as (app, core, _sent, dispatch):
            await dispatch(f"esc:claim:{ESCALATION}")
            await dispatch(f"esc:resolve:{ESCALATION}")
            await dispatch("text:Не сохранять после изменения контекста.")
            pending = app.user_data[ACTOR]["escalation_resolution_pending"]
            save = f"esc:note-save:{pending['nonce']}"
            if change == "assignment":
                core.assigned = False
            elif change == "menu":
                await dispatch("/menu")
            else:
                await dispatch(f"case:escalation:{UUID(int=999)}")
            await dispatch(save)
            assert not core.posts and not core.conclusions

    asyncio.run(scenario())


def test_guided_handlers_do_not_mutate_the_legacy_factory():
    from telegram_gateway.quick_intake_runtime import build_application_with_quick_intake

    legacy = build_application_with_quick_intake("123456:unit_test_token_value_1234567890")
    composed = build_application_with_case_experience("123456:unit_test_token_value_1234567890")
    legacy_conversation = next(item for item in legacy.handlers[0]
                               if getattr(item, "name", None) == "administrator-case-intake-v1")
    composed_conversation = next(item for item in composed.handlers[0]
                                 if getattr(item, "name", None) == "administrator-case-intake-v1")
    state = gateway_bot.WizardState.SERVICE_DATE
    assert legacy_conversation.states[state][0] is not composed_conversation.states[state][0]
    assert legacy_conversation.states[state][0].callback is not (
        composed_conversation.states[state][0].callback
    )
