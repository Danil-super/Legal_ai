"""Opt-in late-result transport: source-bound Core output, never model authority."""

import asyncio
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest

from agent_orchestrator.legal_core_client import (
    LegalCoreClient,
    LegalCoreEndpoint,
    LegalCoreProtocolError,
)
from agent_orchestrator.reasoning import ReasoningResult
from legal_core.analysis_contracts import PreparationResult
from test_legal_core_client import CASE_ID, IDEMPOTENCY_KEY, _context


def preparation_wire(context, status="NEEDS_FACTS"):
    result = {
        "resultVersion": 2, "status": status,
        "source": {
            "operationId": str(uuid4()), "caseId": str(context.case_id),
            "factSnapshotSha256": context.fact_snapshot_sha256,
            "context": {
                "asOfDate": context.as_of_date.isoformat(),
                "evidenceTraceSha256": context.evidence_trace_sha256,
                "clinicDocumentContextTraceSha256": context.clinic_document_context_trace_sha256,
                "riskPolicyVersion": context.risk_policy_version,
            },
        },
    }
    if status == "NEEDS_FACTS":
        result.update(
            reasonCode="FACTUAL_CLARIFICATION_REQUIRED",
            clarificationRequests=[{"questionId": "RESPONSE_PURPOSE", "serviceSlot": None}],
            missingFactKeys=[],
        )
    elif status == "LIMITED":
        result.update(reasonCode="LEGAL_EVIDENCE_UNAVAILABLE", missingFactKeys=[])
    else:
        result.update(escalationId=str(uuid4()), riskLevel="HIGH",
                      riskPolicyVersion=context.risk_policy_version)
    return result


@pytest.mark.parametrize("status", ["NEEDS_FACTS", "LIMITED", "HUMAN_REVIEW"])
def test_opt_in_submission_preserves_typed_intermediate_and_fenced_identity(status):
    context = _context()
    result = preparation_wire(context, status)
    requests = []
    token = uuid4()

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=result)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core:8000", "k" * 32), client=http)
            return await client.submit_reasoning(
                context=context, reasoning=ReasoningResult((), (), (), None),
                telegram_user_id=123, idempotency_key=IDEMPOTENCY_KEY, result_version=2,
                job_id=IDEMPOTENCY_KEY, job_token=token,
            )
    actual = asyncio.run(scenario())
    assert actual == PreparationResult.model_validate(result)
    assert requests[0].url.path == f"/v1/cases/{CASE_ID}/preparation-submissions"
    assert requests[0].headers["x-analysis-job-token"] == str(token)
    assert requests[0].headers["idempotency-key"] == str(IDEMPOTENCY_KEY)
    assert requests[0].headers["x-telegram-user-id"] == "123"
    assert "x-clinic-id" not in requests[0].headers


@pytest.mark.parametrize("field", [
    "caseId", "factSnapshotSha256", "asOfDate", "evidenceTraceSha256",
    "clinicDocumentContextTraceSha256", "riskPolicyVersion", "context", "version", "report",
])
def test_late_preparation_response_must_match_the_actual_retrieved_context(field):
    context = _context()
    result = preparation_wire(context)
    if field in {"caseId", "factSnapshotSha256"}:
        result["source"][field] = str(uuid4()) if field == "caseId" else "f" * 64
    elif field == "context":
        result["source"][field] = None
    elif field == "version":
        result["resultVersion"] = 3
    elif field == "report":
        result["report"] = None
    else:
        result["source"]["context"][field] = (
            "2026-08-30" if field == "asOfDate" else
            "other-policy" if field == "riskPolicyVersion" else "f" * 64
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=result),
        )) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core:8000", "k" * 32), client=http)
            await client.submit_reasoning(
                context=context, reasoning=ReasoningResult((), (), (), None),
                telegram_user_id=123, idempotency_key=IDEMPOTENCY_KEY, result_version=2,
            )
    with pytest.raises(LegalCoreProtocolError):
        asyncio.run(scenario())


def test_wrong_analysis_context_case_is_rejected_before_any_model_can_read_it():
    response = deepcopy(_context().model_dump(mode="json", by_alias=True))
    response["caseId"] = str(uuid4())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response),
        )) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core:8000", "k" * 32), client=http)
            await client.get_analysis_context(case_id=CASE_ID, telegram_user_id=123)
    with pytest.raises(LegalCoreProtocolError):
        asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["context", "LIMITED", "HUMAN_REVIEW"])
def test_precontext_transport_preserves_the_actual_core_gate_and_lease(kind):
    context = _context()
    wire = context.model_dump(mode="json", by_alias=True)
    if kind != "context":
        wire = preparation_wire(context, kind)
        wire["source"]["context"] = None
    requests, token = [], uuid4()

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=wire)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core:8000", "k" * 32), client=http)
            return await client.prepare_analysis_context(
                case_id=CASE_ID, telegram_user_id=123, idempotency_key=IDEMPOTENCY_KEY,
                job_id=IDEMPOTENCY_KEY, job_token=token,
            )
    result = asyncio.run(scenario())
    assert result.model_dump(mode="json", by_alias=True) == wire
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"/v1/cases/{CASE_ID}/preparation-context"
    assert requests[0].headers["x-analysis-job-token"] == str(token)
    assert requests[0].headers["idempotency-key"] == str(IDEMPOTENCY_KEY)


@pytest.mark.parametrize("tamper", ["case", "context", "version", "report"])
def test_precontext_transport_refuses_incoherent_or_fabricated_context(tamper):
    context = _context()
    wire = preparation_wire(context, "LIMITED")
    wire["source"]["context"] = None
    if tamper == "case":
        wire["source"]["caseId"] = str(uuid4())
    elif tamper == "context":
        wire["source"]["context"] = preparation_wire(context)["source"]["context"]
    elif tamper == "version":
        wire["resultVersion"] = True
    else:
        wire["report"] = None

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=wire),
        )) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core:8000", "k" * 32), client=http)
            await client.prepare_analysis_context(
                case_id=CASE_ID, telegram_user_id=123, idempotency_key=IDEMPOTENCY_KEY,
            )
    with pytest.raises(LegalCoreProtocolError):
        asyncio.run(scenario())


def test_precontext_transport_checks_a_usable_context_case_before_model_use():
    wire = _context().model_dump(mode="json", by_alias=True)
    wire["caseId"] = str(uuid4())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=wire),
        )) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core:8000", "k" * 32), client=http)
            await client.prepare_analysis_context(
                case_id=CASE_ID, telegram_user_id=123, idempotency_key=IDEMPOTENCY_KEY,
            )
    with pytest.raises(LegalCoreProtocolError):
        asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["NEEDS_FACTS", "LIMITED", "HUMAN_REVIEW"])
def test_context_preparation_can_return_an_audited_retained_late_result(kind):
    wire = preparation_wire(_context(), kind)
    if kind == "LIMITED":
        wire["reasonCode"] = "EVIDENCE_NOT_VERIFIED"

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=wire),
        )) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core:8000", "k" * 32), client=http)
            return await client.prepare_analysis_context(
                case_id=CASE_ID, telegram_user_id=123, idempotency_key=IDEMPOTENCY_KEY,
            )
    assert asyncio.run(scenario()) == PreparationResult.model_validate(wire)
