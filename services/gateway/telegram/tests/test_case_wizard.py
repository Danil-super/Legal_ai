# ruff: noqa: RUF001
import asyncio
import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx2
import pytest
from telegram.ext import ConversationHandler
from telegram_gateway.bot import (
    LEGAL_CORE_CLIENT_KEY,
    WizardState,
    _draft_from_data,
    _persist_transition,
    build_application,
    cancel_case,
    case_start,
    choose_hospitalization,
    choose_lawyer,
    confirm_case,
    grant_access,
    grant_pilot,
    prompt_admin_grant_access,
    prompt_admin_grant_pilot,
    record_admin_grant_access,
    record_admin_grant_pilot,
    record_lawyer_deadline,
    resume_intake_draft,
    resume_workflow,
    whoami,
)
from telegram_gateway.case_wizard import (
    CaseDraft,
    LegalCoreApiError,
    LegalCoreClient,
    facts_from_draft,
    parse_date_answer,
    parse_iso_date,
    parse_ruble_amount_to_kopecks,
    telegram_summary_from_report,
)


class FakeMessage:
    def __init__(self) -> None:
        self.text_replies: list[str] = []
        self.documents: list[dict[str, object]] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.text_replies.append(text)

    async def reply_document(self, **kwargs: object) -> None:
        self.documents.append(kwargs)


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class FakeLegalCore:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response

    async def get_actor(self, telegram_user_id: int) -> dict[str, Any]:
        del telegram_user_id
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    async def create_case(self, telegram_user_id: int, idempotency_key: UUID) -> dict[str, Any]:
        del telegram_user_id, idempotency_key
        raise AssertionError("case must not be created before confirmation")

    async def create_intake_draft(self, telegram_user_id: int) -> dict[str, Any]:
        del telegram_user_id
        if isinstance(self.response, Exception):
            raise self.response
        return {
            "id": "9d0dd02f-cfd9-498a-85e4-c30b53abca88",
            "wizardState": "INCIDENT",
            "revision": 1,
        }

    async def save_intake_draft(self, *args: object, **kwargs: object) -> dict[str, Any]:
        del args, kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return {"revision": 2}

    async def archive_intake_draft(self, *args: object, **kwargs: object) -> dict[str, Any]:
        del args, kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return {"revision": 3}

    async def list_intake_drafts(self, telegram_user_id: int) -> dict[str, Any]:
        del telegram_user_id
        if isinstance(self.response, Exception):
            raise self.response
        return {"items": []}

    async def get_intake_draft(self, draft_id: UUID, telegram_user_id: int) -> dict[str, Any]:
        del telegram_user_id
        if isinstance(self.response, Exception):
            raise self.response
        return {
            "id": str(draft_id),
            "wizardState": "SERVICE_TYPE",
            "revision": 2,
            "draftData": {"incident_type": "QUALITY_COMPLAINT"},
        }

    async def grant_subscription(
        self,
        telegram_user_id: int,
        target_telegram_user_id: int,
        *,
        plan_code: str = "MVP_MANUAL",
        pilot_days: int | None = None,
    ) -> dict[str, Any]:
        del telegram_user_id
        if isinstance(self.response, Exception):
            raise self.response
        return {
            "telegramUserId": target_telegram_user_id,
            "clinicName": "Новая стоматология",
            "planCode": plan_code,
            "status": "ACTIVE",
            "endsAt": None if pilot_days is None else "2026-09-30T00:00:00Z",
        }


class FakeReportPipeline:
    def __init__(self) -> None:
        self.steps: list[str] = []

    def workflow_response(self) -> dict[str, Any]:
        return {
            "workflowId": "9d0dd02f-cfd9-498a-85e4-c30b53abca88",
            "state": "SUCCEEDED",
            "case": {"publicNumber": "DL-2026-000001"},
            "report": {
                "id": "a50b755e-6a51-4939-ae03-fb17c9c07719",
                "reportJson": canonical_report(),
            },
        }

    async def submit_workflow(self, *args: object) -> dict[str, Any]:
        del args
        self.steps.append("submit")
        return self.workflow_response()

    async def get_workflow(self, *args: object) -> dict[str, Any]:
        del args
        self.steps.append("recover")
        return self.workflow_response()

    async def download_pdf(self, *args: object) -> bytes:
        del args
        self.steps.append("pdf")
        return b"%PDF-synthetic"

    async def archive_intake_draft(self, *args: object, **kwargs: object) -> dict[str, Any]:
        del args, kwargs
        self.steps.append("archive")
        return {"revision": 3}


def test_parse_iso_date_accepts_only_real_non_future_iso_dates() -> None:
    assert parse_iso_date("2026-08-21", today="2026-08-22") == "2026-08-21"
    assert parse_iso_date("21.08.2026", today="2026-08-22") is None
    assert parse_iso_date("2026-02-30", today="2026-08-22") is None
    assert parse_iso_date("2026-08-23", today="2026-08-22") is None


def test_parse_date_answer_preserves_explicit_unknown_without_making_up_a_date() -> None:
    assert parse_date_answer("неизвестно", today="2026-08-22") == {
        "date": None,
        "precision": "UNKNOWN",
    }
    assert parse_date_answer("2026-08-21", today="2026-08-22") == {
        "date": "2026-08-21",
        "precision": "EXACT",
    }
    assert parse_date_answer("завтра", today="2026-08-22") is None


def test_ruble_amount_is_finite_and_converted_to_integer_kopecks() -> None:
    assert parse_ruble_amount_to_kopecks("12,34") == 1_234
    assert parse_ruble_amount_to_kopecks("0.01") == 1
    assert parse_ruble_amount_to_kopecks("12.345") is None
    assert parse_ruble_amount_to_kopecks("NaN") is None
    assert parse_ruble_amount_to_kopecks("Infinity") is None


def canonical_report() -> dict[str, Any]:
    return {
        "schemaVersion": "dental-case-report.v1",
        "case": {"publicNumber": "DL-2026-000001", "status": "ANALYSIS_BLOCKED"},
        "summary": {
            "neutralDescription": "Пациент сообщил об обезличенной проблемной ситуации."
        },
        "missingFacts": [],
        "recommendations": {"status": "NOT_AVAILABLE"},
        "draftResponse": {"status": "NOT_AVAILABLE"},
        "legalBasis": {"status": "NOT_AVAILABLE"},
        "disclaimer": "Внутренняя карточка. Не является юридическим заключением.",
    }


def test_telegram_summary_is_rendered_from_canonical_report_and_rejects_wrong_schema() -> None:
    summary = telegram_summary_from_report(canonical_report())

    assert "DL-2026-000001" in summary
    assert "АНАЛИЗ ЗАБЛОКИРОВАН" in summary
    assert "обезличенной проблемной ситуации" in summary

    invalid = canonical_report()
    invalid["schemaVersion"] = "unknown"
    with pytest.raises(ValueError, match="canonical report"):
        telegram_summary_from_report(invalid)


def test_complete_draft_maps_to_legal_core_contract_without_clinic_id() -> None:
    draft = CaseDraft(
        incident_type="QUALITY_COMPLAINT",
        service_type="Установка коронки",
        service_date="2026-06-01",
        incident_date="2026-07-01",
        claim_date="2026-07-02",
        problem_summary="Пациент сообщил о сколе конструкции без указания ФИО.",
        patient_demand="NO_SPECIFIC_DEMAND",
        formal_claim=False,
        harm_claimed=False,
        regulator_or_court=False,
        documents_status="PARTIAL",
    )

    payload = facts_from_draft(draft)

    assert payload[0]["factKey"] == "INCIDENT_TYPES"
    assert next(item for item in payload if item["factKey"] == "PRIMARY_INCIDENT_TYPE")[
        "value"
    ] == {"value": "QUALITY_COMPLAINT"}
    assert len(payload) == 14
    documents = next(item for item in payload if item["factKey"] == "CLINIC_DOCUMENTS")
    assert set(documents["value"].values()) == {"UNKNOWN"}
    assert not any("clinic" in key.lower() for item in payload for key in item)
    encoded = json.dumps(payload).lower()
    assert "clinicid" not in encoded
    assert "clinic_id" not in encoded


def test_authority_deadline_maps_without_a_formal_patient_claim() -> None:
    draft = CaseDraft(
        incident_type="QUALITY_COMPLAINT",
        service_type="Лечение кариеса",
        service_date="2026-06-01",
        incident_date="2026-07-01",
        claim_date="2026-07-02",
        problem_summary="Клиника получила обезличенное обращение контролирующего органа.",
        patient_demand="NO_SPECIFIC_DEMAND",
        formal_claim=False,
        harm_claimed=False,
        regulator_or_court=True,
        documents_status="COMPLETE",
        authority_kind="Территориальный орган Росздравнадзора",
        authority_document_date="2026-07-03",
        response_deadline="2026-07-20",
    )

    payload = facts_from_draft(draft)

    response_deadlines = [
        item for item in payload if item["factKey"] == "RESPONSE_DEADLINE"
    ]
    assert response_deadlines == [
        {
            "factKey": "RESPONSE_DEADLINE",
            "valueType": "DATE",
            "value": {"date": "2026-07-20", "precision": "EXACT"},
            "sourceType": "USER_STATEMENT",
        }
    ]


def test_draft_maps_unknown_dates_and_signals_without_converting_them_to_no() -> None:
    draft = CaseDraft(
        incident_type="QUALITY_COMPLAINT",
        service_type="Лечение кариеса",
        service_date={"date": None, "precision": "UNKNOWN"},
        incident_date={"date": None, "precision": "UNKNOWN"},
        claim_date={"date": None, "precision": "UNKNOWN"},
        problem_summary="Администратор пока не получил подтверждающие сведения от врача.",
        patient_demand="NO_SPECIFIC_DEMAND",
        formal_claim="UNKNOWN",
        harm_claimed="UNKNOWN",
        regulator_or_court="UNKNOWN",
        hospitalization="UNKNOWN",
        documents_status="PARTIAL",
    )

    payload = facts_from_draft(draft)

    service_date = next(item for item in payload if item["factKey"] == "SERVICE_DATE")
    formal_claim = next(item for item in payload if item["factKey"] == "FORMAL_CLAIM")
    harm = next(item for item in payload if item["factKey"] == "HARM_CLAIMED")
    hospital = next(item for item in payload if item["factKey"] == "HOSPITALIZATION")
    authority = next(item for item in payload if item["factKey"] == "REGULATOR_OR_COURT")

    assert service_date["value"] == {"date": None, "precision": "UNKNOWN"}
    assert formal_claim["value"] == {"state": "UNKNOWN"}
    assert harm["value"] == {"state": "UNKNOWN"}
    assert hospital["value"] == {"state": "UNKNOWN"}
    assert authority["value"] == {"state": "UNKNOWN"}
    assert not any(item["factKey"] == "CLAIM_RECEIVED_AT" for item in payload)


def test_wizard_data_keeps_unknown_date_shape_until_submission() -> None:
    draft = _draft_from_data(
        {
            "incident_type": "QUALITY_COMPLAINT",
            "service_type": "Лечение кариеса",
            "service_date": {"date": None, "precision": "UNKNOWN"},
            "incident_date": "2026-07-01",
            "claim_date": "2026-07-02",
            "problem_summary": "Администратор ожидает уточнения даты оказания услуги.",
            "patient_demand": "NO_SPECIFIC_DEMAND",
            "formal_claim": "NO",
            "harm_claimed": "NO",
            "regulator_or_court": "NO",
            "documents_status": "PARTIAL",
        }
    )

    payload = facts_from_draft(draft)

    assert next(item for item in payload if item["factKey"] == "SERVICE_DATE")["value"] == {
        "date": None,
        "precision": "UNKNOWN",
    }


def test_draft_maps_lawyer_and_regulator_threat_signals() -> None:
    draft = CaseDraft(
        incident_type="QUALITY_COMPLAINT",
        service_type="Лечение кариеса",
        service_date="2026-06-01",
        incident_date="2026-07-01",
        claim_date="2026-07-02",
        problem_summary="Клиника получила сообщение от представителя пациента.",
        patient_demand="NO_SPECIFIC_DEMAND",
        formal_claim="NO",
        harm_claimed="NO",
        regulator_or_court="NO",
        lawyer_contact="YES",
        representative_authority="UNKNOWN",
        regulator_threat="UNKNOWN",
        response_deadline="2026-07-20",
        documents_status="PARTIAL",
    )

    payload = facts_from_draft(draft)

    assert next(item for item in payload if item["factKey"] == "LAWYER_CONTACT")["value"] == {
        "state": "YES"
    }
    assert next(
        item for item in payload if item["factKey"] == "REPRESENTATIVE_AUTHORITY"
    )["value"] == {"state": "UNKNOWN"}
    assert next(item for item in payload if item["factKey"] == "REGULATOR_THREAT")["value"] == {
        "state": "UNKNOWN"
    }


def test_workflow_client_uses_durable_uuid_and_never_sends_clinic_context() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            201,
            json={
                "workflowId": "00a55604-acbd-4a26-9252-102f25cfa9c6",
                "state": "SUCCEEDED",
            },
        )

    transport = httpx2.MockTransport(handler)

    async def scenario() -> None:
        async with httpx2.AsyncClient(
            base_url="http://legal-core:8000", transport=transport
        ) as http:
            client = LegalCoreClient(http)
            await client.submit_workflow(
                UUID("00a55604-acbd-4a26-9252-102f25cfa9c6"),
                [],
                telegram_user_id=7_000_000_001,
            )

    asyncio.run(scenario())

    request = requests[0]
    assert request.headers["x-telegram-user-id"] == "7000000001"
    assert request.url.path == (
        "/v1/telegram-case-workflows/00a55604-acbd-4a26-9252-102f25cfa9c6/submissions"
    )
    assert "idempotency-key" not in request.headers
    assert "x-clinic-id" not in request.headers
    assert "clinicId" not in request.content.decode()


def test_actor_probe_is_read_only_and_uses_only_telegram_identity() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"role": "CLINIC_ADMIN"})

    async def scenario() -> None:
        async with httpx2.AsyncClient(
            base_url="http://legal-core:8000", transport=httpx2.MockTransport(handler)
        ) as http:
            await LegalCoreClient(http).get_actor(7_000_000_001)

    asyncio.run(scenario())

    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v1/actor"
    assert requests[0].headers["x-telegram-user-id"] == "7000000001"
    assert "idempotency-key" not in requests[0].headers
    assert requests[0].content == b""


def test_whoami_returns_only_telegram_identifier() -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )

    asyncio.run(whoami(update, None))

    assert message.text_replies == ["👤 Ваш Telegram ID: 7000000001"]


def test_case_start_handles_unknown_administrator_without_leaking_details() -> None:
    message = FakeMessage()
    query = FakeQuery("case:start")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=9_999_999_999),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={
            LEGAL_CORE_CLIENT_KEY: FakeLegalCore(
                LegalCoreApiError(403, "ACTOR_NOT_AUTHORIZED", "not mapped")
            )
        },
        user_data={},
    )

    result = asyncio.run(case_start(update, context))

    assert result == ConversationHandler.END
    assert query.answers == [(None, False)]
    assert "не подключён" in message.text_replies[0].lower()
    assert "clinic" not in message.text_replies[0].lower()
    assert context.user_data == {}


def test_grant_access_accepts_only_one_numeric_target_id() -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={LEGAL_CORE_CLIENT_KEY: FakeLegalCore({"role": "CLINIC_ADMIN"})},
        args=["7000000002"],
    )

    asyncio.run(grant_access(update, context))

    assert "7000000002" in message.text_replies[0]
    assert "доступ" in message.text_replies[0].lower()


def test_grant_access_does_not_reveal_target_details_to_non_owner() -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={
            LEGAL_CORE_CLIENT_KEY: FakeLegalCore(
                LegalCoreApiError(403, "OWNER_REQUIRED", "owner is required")
            )
        },
        args=["7000000002"],
    )

    asyncio.run(grant_access(update, context))

    assert "только владельцу" in message.text_replies[0].lower()
    assert "7000000002" not in message.text_replies[0]


def test_owner_panel_button_collects_id_and_grants_access_without_a_command() -> None:
    message = FakeMessage()
    query = FakeQuery("admin:grant")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={LEGAL_CORE_CLIENT_KEY: FakeLegalCore({"role": "CLINIC_ADMIN"})},
        user_data={},
    )

    asyncio.run(prompt_admin_grant_access(update, context))
    message.text = "7000000002"
    asyncio.run(record_admin_grant_access(update, context))

    assert query.answers == [(None, False)]
    assert "введите telegram id" in message.text_replies[0].lower()
    assert "доступ выдан" in message.text_replies[1].lower()
    assert context.user_data == {}


def test_owner_can_grant_a_thirty_day_free_pilot_from_the_admin_panel() -> None:
    message = FakeMessage()
    query = FakeQuery("admin:pilot")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={LEGAL_CORE_CLIENT_KEY: FakeLegalCore({"role": "CLINIC_ADMIN"})},
        user_data={},
    )

    asyncio.run(prompt_admin_grant_pilot(update, context))
    message.text = "7000000002"
    asyncio.run(record_admin_grant_pilot(update, context))

    assert query.answers == [(None, False)]
    assert "30 дней" in message.text_replies[0]
    assert "pilot" in message.text_replies[1].lower()
    assert context.user_data == {}


def test_grant_pilot_rejects_an_out_of_range_duration() -> None:
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={LEGAL_CORE_CLIENT_KEY: FakeLegalCore({"role": "CLINIC_ADMIN"})},
        args=["7000000002", "91"],
    )

    asyncio.run(grant_pilot(update, context))

    assert "от 1 до 90" in message.text_replies[0]


def test_hospitalization_answer_advances_to_the_lawyer_question() -> None:
    message = FakeMessage()
    query = FakeQuery("case:hospital:yes")
    update = SimpleNamespace(callback_query=query, effective_message=message)
    context = SimpleNamespace(user_data={"case_wizard": {"harm_claimed": "YES"}})

    result = asyncio.run(choose_hospitalization(update, context))

    assert result == WizardState.LAWYER
    assert context.user_data["case_wizard"]["hospitalization"] == "YES"
    assert message.text_replies == [
        "9/12. Связывался ли с клиникой представитель или юрист пациента?"
    ]


def test_accepted_wizard_transition_saves_the_draft_before_next_question() -> None:
    message = FakeMessage()
    query = FakeQuery("case:hospital:yes")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={LEGAL_CORE_CLIENT_KEY: FakeLegalCore({"role": "CLINIC_ADMIN"})},
        user_data={
            "case_wizard": {
                "workflow_id": "9d0dd02f-cfd9-498a-85e4-c30b53abca88",
                "draft_id": "9d0dd02f-cfd9-498a-85e4-c30b53abca88",
                "draft_revision": 1,
                "harm_claimed": "YES",
            }
        },
    )

    result = asyncio.run(_persist_transition(choose_hospitalization, update, context))

    assert result == WizardState.LAWYER
    assert context.user_data["case_wizard"]["draft_revision"] == 2


def test_resuming_a_draft_restores_its_next_question() -> None:
    draft_id = "9d0dd02f-cfd9-498a-85e4-c30b53abca88"
    message = FakeMessage()
    query = FakeQuery(f"case:draft:{draft_id}")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={LEGAL_CORE_CLIENT_KEY: FakeLegalCore({"role": "CLINIC_ADMIN"})}, user_data={}
    )

    result = asyncio.run(resume_intake_draft(update, context))

    assert result == WizardState.SERVICE_TYPE
    assert context.user_data["case_wizard"]["draft_id"] == draft_id
    assert context.user_data["case_wizard"]["draft_revision"] == 2
    assert "сохранённого шага" in message.text_replies[0]
    assert "стоматологическая услуга" in message.text_replies[1]


def test_case_start_explains_inactive_subscription_without_leaking_tenant_details() -> None:
    message = FakeMessage()
    query = FakeQuery("case:start")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={
            LEGAL_CORE_CLIENT_KEY: FakeLegalCore(
                LegalCoreApiError(403, "SUBSCRIPTION_INACTIVE", "inactive")
            )
        },
        user_data={},
    )

    result = asyncio.run(case_start(update, context))

    assert result == ConversationHandler.END
    assert "подписк" in message.text_replies[0].lower()
    assert "clinic" not in message.text_replies[0].lower()
    assert context.user_data == {}


def test_case_start_checks_access_without_creating_case_and_opens_incident_question() -> None:
    message = FakeMessage()
    query = FakeQuery("case:start")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={
            LEGAL_CORE_CLIENT_KEY: FakeLegalCore({"role": "CLINIC_ADMIN"})
        },
        user_data={},
    )

    result = asyncio.run(case_start(update, context))

    assert result == WizardState.INCIDENT
    assert "case_id" not in context.user_data["case_wizard"]
    assert UUID(context.user_data["case_wizard"]["workflow_id"])
    assert "без ФИО" in message.text_replies[0]


def test_lawyer_unknown_is_preserved_and_does_not_become_a_negative_answer() -> None:
    message = FakeMessage()
    query = FakeQuery("case:lawyer:unknown")
    update = SimpleNamespace(callback_query=query, effective_message=message)
    context = SimpleNamespace(bot_data={}, user_data={"case_wizard": {}})

    result = asyncio.run(choose_lawyer(update, context))

    assert result == WizardState.AUTHORITY
    assert context.user_data["case_wizard"]["lawyer_contact"] == "UNKNOWN"
    assert "суд" in message.text_replies[0].lower()


def test_lawyer_deadline_does_not_overwrite_an_earlier_claim_deadline() -> None:
    message = FakeMessage()
    message.text = "2026-07-20"
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(
        user_data={
            "case_wizard": {
                "response_deadline": {"date": "2026-07-15", "precision": "EXACT"}
            }
        }
    )

    result = asyncio.run(record_lawyer_deadline(update, context))

    assert result == WizardState.AUTHORITY
    assert context.user_data["case_wizard"]["response_deadline"] == {
        "date": "2026-07-15",
        "precision": "EXACT",
    }


def test_application_serializes_conversation_updates_and_registers_wizard() -> None:
    application = build_application("123456:unit_test_token_value_1234567890")
    handlers = [handler for group in application.handlers.values() for handler in group]

    assert application.update_processor.max_concurrent_updates == 1
    assert any(isinstance(handler, ConversationHandler) for handler in handlers)
    assert isinstance(application.handlers[0][0], ConversationHandler)


def test_confirmation_finalizes_case_builds_report_and_returns_pdf() -> None:
    message = FakeMessage()
    query = FakeQuery("case:confirm:9d0dd02f-cfd9-498a-85e4-c30b53abca88")
    pipeline = FakeReportPipeline()
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={LEGAL_CORE_CLIENT_KEY: pipeline},
        user_data={
                "case_wizard": {
                    "workflow_id": "9d0dd02f-cfd9-498a-85e4-c30b53abca88",
                    "draft_id": "9d0dd02f-cfd9-498a-85e4-c30b53abca88",
                    "draft_revision": 2,
                "incident_type": "QUALITY_COMPLAINT",
                "service_type": "Установка коронки",
                "service_date": "2026-06-01",
                "incident_date": "2026-07-01",
                "claim_date": "2026-07-02",
                "problem_summary": "Пациент сообщил об обезличенной проблемной ситуации.",
                "patient_demand": "NO_SPECIFIC_DEMAND",
                "formal_claim": False,
                "harm_claimed": False,
                "regulator_or_court": False,
                "documents_status": "PARTIAL",
            }
        },
    )

    result = asyncio.run(confirm_case(update, context))

    assert result == ConversationHandler.END
    assert pipeline.steps == ["submit", "pdf", "archive"]
    assert any("АНАЛИЗ ЗАБЛОКИРОВАН" in text for text in message.text_replies)
    assert message.documents[0]["caption"].startswith("✅ Отчёт по кейсу DL-2026-000001")
    assert context.user_data == {}


def test_confirmation_callback_recovers_same_report_after_process_state_loss() -> None:
    message = FakeMessage()
    workflow_id = "9d0dd02f-cfd9-498a-85e4-c30b53abca88"
    query = FakeQuery(f"case:confirm:{workflow_id}")
    pipeline = FakeReportPipeline()
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=7_000_000_001),
        effective_message=message,
    )
    context = SimpleNamespace(
        bot_data={LEGAL_CORE_CLIENT_KEY: pipeline},
        user_data={},
    )

    result = asyncio.run(resume_workflow(update, context))

    assert result is None
    assert pipeline.steps == ["recover", "pdf"]
    assert message.documents[0]["caption"].startswith("✅ Отчёт по кейсу DL-2026-000001")
    assert len(query.data.encode()) <= 64


def test_cancel_does_not_create_or_claim_deletion_of_backend_case() -> None:
    message = FakeMessage()
    update = SimpleNamespace(callback_query=None, effective_message=message)
    context = SimpleNamespace(
        user_data={"case_wizard": {"workflow_id": str(UUID(int=1))}}
    )

    result = asyncio.run(cancel_case(update, context))

    assert result == ConversationHandler.END
    assert "остановлен" in message.text_replies[0].lower()
    assert "удален" not in message.text_replies[0].lower()
