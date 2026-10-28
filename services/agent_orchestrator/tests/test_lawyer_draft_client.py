"""Private Core metadata is fenced and revalidated before optional model work."""

import asyncio
import json
from copy import deepcopy
from uuid import UUID

import httpx
import pytest

from agent_orchestrator.legal_core_client import (
    LegalCoreClient,
    LegalCoreEndpoint,
    LegalCoreError,
    LegalCoreProtocolError,
)
from legal_core.lawyer_draft_contracts import LawyerDraftContextRequest
from test_lawyer_draft_generation import context
from test_lawyer_draft_orchestration import TOKEN, retained_view
from test_main import IDEMPOTENCY_KEY, INTERNAL_KEY, _reasoning


def run(mode, *, mutate=None, failure=None, bad_lease=False, bad_binding=False, status=200):
    bundle = context()
    binding = LawyerDraftContextRequest(
        expectedPreparationOperationId=bundle.source.operation_id,
        expectedFactSnapshotSha256=bundle.source.fact_snapshot_sha256,
        expectedRiskPolicyVersion=bundle.context.risk_policy_version,
    )
    if bad_binding:
        binding.__dict__["expected_fact_snapshot_sha256"] = "SYNTHETIC_PRIVATE_INPUT"
    wire = (bundle.model_dump(mode="json", by_alias=True) if mode == "context" else
            retained_view(bundle.source) if mode == "submit" else None)
    if mutate:
        wire = mutate(deepcopy(wire), bundle)
    calls = []

    async def handler(request):
        calls.append(request)
        if failure == "transport":
            raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_INPUT", request=request)
        if failure == "json":
            return httpx.Response(200, text="SYNTHETIC_PRIVATE_INPUT")
        if failure == "refusal":
            return httpx.Response(403, json={"error": {
                "code": "SYNTHETIC_PRIVATE_INPUT", "message": "SYNTHETIC_PRIVATE_INPUT",
            }})
        return httpx.Response(status, content=json.dumps(wire),
                              headers={"content-type": "application/json"})

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core.test", INTERNAL_KEY),
                                     client=http)
            arguments = {"telegram_user_id": 123, "idempotency_key": IDEMPOTENCY_KEY,
                         "job_id": UUID(int=9001) if bad_lease else IDEMPOTENCY_KEY,
                         "job_token": TOKEN}
            if mode == "context":
                return await client.get_lawyer_draft_context(
                    case_id=bundle.source.case_id, binding=binding, **arguments,
                )
            if mode == "lookup":
                return await client.lookup_lawyer_work_product(
                    case_id=bundle.source.case_id, binding=binding, **arguments,
                )
            return await client.submit_lawyer_work_product(
                context=bundle, reasoning=_reasoning(), **arguments,
            )
    return lambda: asyncio.run(invoke()), calls


@pytest.mark.parametrize("mode", ["context", "lookup", "submit"])
def test_private_clients_keep_the_actor_key_lease_and_job_idempotency(mode):
    invoke, calls = run(mode)
    result = invoke()
    assert result is not None if mode != "lookup" else result is None
    request = calls[0]
    assert request.method == "POST"
    assert request.headers["X-Agent-Internal-Key"] == INTERNAL_KEY
    assert request.headers["X-Analysis-Job-Id"] == str(IDEMPOTENCY_KEY)
    assert request.headers["Idempotency-Key"] == str(IDEMPOTENCY_KEY)
    assert request.headers["X-Analysis-Job-Token"] == str(TOKEN)
    assert "x-clinic-id" not in request.headers


@pytest.mark.parametrize("mode", ["context", "lookup", "submit"])
@pytest.mark.parametrize("failure", ["transport", "json", "refusal"])
def test_private_client_errors_carry_no_input_or_transport_exception_context(mode, failure):
    invoke, _ = run(mode, failure=failure)
    with pytest.raises((LegalCoreError, LegalCoreProtocolError)) as caught:
        invoke()
    assert "SYNTHETIC_PRIVATE_INPUT" not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize("mode", ["context", "lookup", "submit"])
def test_wrong_job_binding_is_rejected_without_an_http_request(mode):
    invoke, calls = run(mode, bad_lease=True)
    with pytest.raises(LegalCoreProtocolError):
        invoke()
    assert not calls


@pytest.mark.parametrize("mode", ["context", "lookup"])
def test_constructed_invalid_request_is_reparsed_without_echoing_private_input(mode):
    invoke, calls = run(mode, bad_binding=True)
    with pytest.raises(LegalCoreProtocolError) as caught:
        invoke()
    assert not calls and "SYNTHETIC_PRIVATE_INPUT" not in str(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize("field", ["case", "operation", "facts", "policy", "trace"])
def test_context_must_match_the_requested_retained_preparation(field):
    def mutate(wire, _bundle):
        if field == "case":
            wire["source"]["caseId"] = wire["context"]["caseId"] = str(UUID(int=9002))
        elif field == "operation":
            wire["source"]["operationId"] = str(UUID(int=9002))
        elif field == "facts":
            wire["source"]["factSnapshotSha256"] = "f" * 64
            wire["context"]["factSnapshotSha256"] = "f" * 64
        elif field == "policy":
            wire["source"]["context"]["riskPolicyVersion"] = "test.foreign"
            wire["context"]["riskPolicyVersion"] = "test.foreign"
        else:
            wire["source"]["context"]["evidenceTraceSha256"] = "e" * 64
        return wire
    invoke, _ = run("context", mutate=mutate)
    with pytest.raises(LegalCoreProtocolError):
        invoke()


@pytest.mark.parametrize("mode", ["lookup", "submit"])
def test_non_null_retained_view_from_a_different_case_is_not_accepted(mode):
    def mutate(_wire, bundle):
        foreign = bundle.source.model_copy(deep=True)
        foreign.case_id = UUID(int=9002)
        return retained_view(foreign)
    invoke, _ = run(mode, mutate=mutate)
    with pytest.raises(LegalCoreProtocolError):
        invoke()


@pytest.mark.parametrize("mode", ["context", "lookup", "submit"])
@pytest.mark.parametrize("status", [202, 204, 302])
def test_nonterminal_or_redirect_status_is_not_accepted_as_a_retained_result(mode, status):
    invoke, _ = run(mode, status=status)
    with pytest.raises(LegalCoreProtocolError):
        invoke()
