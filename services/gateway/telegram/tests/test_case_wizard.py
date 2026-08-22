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
    build_application,
    cancel_case,
    case_start,
    confirm_case,
    whoami,
)
from telegram_gateway.case_wizard import (
    CaseDraft,
    LegalCoreApiError,
    LegalCoreClient,
    facts_from_draft,
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


class FakeReportPipeline:
    def __init__(self) -> None:
        self.steps: list[str] = []

    async def create_case(self, telegram_user_id: int, idempotency_key: UUID) -> dict[str, Any]:
        del telegram_user_id, idempotency_key
        self.steps.append("create")
        return {
            "id": "de39f825-a3ec-47ed-a18a-745334b0eaf2",
            "publicNumber": "DL-2026-000001",
        }

    async def add_facts(self, *args: object) -> dict[str, Any]:
        del args
        self.steps.append("facts")
        return {"missingFacts": []}

    async def finalize_case(self, *args: object) -> dict[str, Any]:
        del args
        self.steps.append("finalize")
        return {"status": "ANALYSIS_BLOCKED"}

    async def create_report(self, *args: object) -> dict[str, Any]:
        del args
        self.steps.append("report")
        return {
            "id": "a50b755e-6a51-4939-ae03-fb17c9c07719",
            "reportJson": canonical_report(),
        }

    async def download_pdf(self, *args: object) -> bytes:
        del args
        self.steps.append("pdf")
        return b"%PDF-synthetic"


def test_parse_iso_date_accepts_only_real_non_future_iso_dates() -> None:
    assert parse_iso_date("2026-08-21", today="2026-08-22") == "2026-08-21"
    assert parse_iso_date("21.08.2026", today="2026-08-22") is None
    assert parse_iso_date("2026-02-30", today="2026-08-22") is None
    assert parse_iso_date("2026-08-23", today="2026-08-22") is None


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
    assert len(payload) == 11
    documents = next(item for item in payload if item["factKey"] == "CLINIC_DOCUMENTS")
    assert set(documents["value"].values()) == {"UNKNOWN"}
    assert not any("clinic" in key.lower() for item in payload for key in item)
    encoded = json.dumps(payload).lower()
    assert "clinicid" not in encoded
    assert "clinic_id" not in encoded


def test_legal_core_client_uses_actor_header_idempotency_and_never_clinic_header() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            201,
            json={
                "id": "de39f825-a3ec-47ed-a18a-745334b0eaf2",
                "publicNumber": "DL-2026-000001",
                "status": "COLLECTING",
                "intakeSchemaVersion": "dental-case-intake.v1",
                "createdAt": "2026-08-22T00:00:00Z",
            },
        )

    transport = httpx2.MockTransport(handler)

    async def scenario() -> None:
        async with httpx2.AsyncClient(
            base_url="http://legal-core:8000", transport=transport
        ) as http:
            client = LegalCoreClient(http)
            await client.create_case(
                telegram_user_id=7_000_000_001,
                idempotency_key=UUID("00a55604-acbd-4a26-9252-102f25cfa9c6"),
            )

    asyncio.run(scenario())

    request = requests[0]
    assert request.headers["x-telegram-user-id"] == "7000000001"
    assert request.headers["idempotency-key"] == "00a55604-acbd-4a26-9252-102f25cfa9c6"
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
    assert "create_key" in context.user_data["case_wizard"]
    assert "без ФИО" in message.text_replies[0]


def test_application_serializes_conversation_updates_and_registers_wizard() -> None:
    application = build_application("123456:unit_test_token_value_1234567890")
    handlers = [handler for group in application.handlers.values() for handler in group]

    assert application.update_processor.max_concurrent_updates == 1
    assert any(isinstance(handler, ConversationHandler) for handler in handlers)
    assert isinstance(application.handlers[0][0], ConversationHandler)


def test_confirmation_finalizes_case_builds_report_and_returns_pdf() -> None:
    message = FakeMessage()
    query = FakeQuery("case:confirm")
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
                "create_key": "8d582656-d20b-40c7-b9a0-b37631be2db1",
                "facts_key": "f6bedc16-03ec-4bcc-bea4-5c37a684c041",
                "finalize_key": "42132740-938a-4f3c-8015-7e729d8eb8db",
                "report_key": "d423943a-1428-49a7-a0e2-fd1338d203ee",
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
    assert pipeline.steps == ["create", "facts", "finalize", "report", "pdf"]
    assert any("АНАЛИЗ ЗАБЛОКИРОВАН" in text for text in message.text_replies)
    assert message.documents[0]["caption"].startswith("✅ Отчёт по кейсу DL-2026-000001")
    assert context.user_data == {}


def test_cancel_does_not_claim_a_partially_created_backend_case_was_deleted() -> None:
    message = FakeMessage()
    update = SimpleNamespace(callback_query=None, effective_message=message)
    context = SimpleNamespace(
        user_data={"case_wizard": {"case_id": "de39f825-a3ec-47ed-a18a-745334b0eaf2"}}
    )

    result = asyncio.run(cancel_case(update, context))

    assert result == ConversationHandler.END
    assert "сохранён" in message.text_replies[0].lower()
    assert "удален" not in message.text_replies[0].lower()
