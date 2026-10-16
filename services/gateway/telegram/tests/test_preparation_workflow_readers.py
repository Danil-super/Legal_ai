"""Gateway transport accepts legacy reports and explicit report-free receipts."""

import asyncio
import json
from uuid import uuid4

import httpx2
import pytest
from telegram_gateway.case_wizard import LegalCoreApiError, LegalCoreClient


def _wire(workflow_id, version):
    case_id = str(uuid4())
    result = {
        "workflowId": str(workflow_id), "state": "SUCCEEDED",
        "case": {
            "id": case_id, "publicNumber": "SYNTHETIC-TRANSPORT", "status": "COLLECTING",
            "intakeSchemaVersion": "dental-case-intake.v2",
            "createdAt": "2026-10-01T00:00:00Z", "earlyEscalationId": None,
        },
    }
    if version == 2:
        result["receiptVersion"] = 2
    else:
        result["report"] = {
            "id": str(uuid4()), "caseId": case_id, "reportVersion": 1,
            "reportJson": {}, "pdfSha256": "a" * 64, "createdAt": "2026-10-01T00:00:00Z",
        }
    return result


def _request(wire, workflow_id, operation):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(201 if operation == "submit" else 200, json=wire)

    async def scenario():
        async with httpx2.AsyncClient(base_url="http://legal-core:8000",
                                      transport=httpx2.MockTransport(handler)) as http:
            client = LegalCoreClient(http)
            if operation == "submit":
                return await client.submit_workflow(workflow_id, [], telegram_user_id=7000000001)
            return await client.get_workflow(workflow_id, telegram_user_id=7000000001)

    return asyncio.run(scenario()), requests


@pytest.mark.parametrize("operation", ["submit", "read"])
@pytest.mark.parametrize("version", [1, 2])
def test_both_workflow_variants_keep_identity_and_user_scope(operation, version):
    workflow = uuid4()
    wire = _wire(workflow, version)
    result, requests = _request(wire, workflow, operation)
    assert result == wire
    assert requests[0].headers["x-telegram-user-id"] == "7000000001"
    assert "x-clinic-id" not in requests[0].headers
    if version == 2:
        assert "report" not in result


@pytest.mark.parametrize("operation", ["submit", "read"])
@pytest.mark.parametrize("malformation", [
    "unknown_version", "float_version", "missing_version", "null_report", "partial_case",
    "wrong_workflow", "legacy_wrong_case", "legacy_missing_report", "patient_field",
])
def test_invalid_or_misbound_receipts_raise_a_bounded_boundary_error(operation, malformation):
    workflow = uuid4()
    wire = _wire(workflow, 1 if malformation.startswith("legacy") else 2)
    if malformation == "unknown_version":
        wire["receiptVersion"] = 3
    elif malformation == "float_version":
        wire["receiptVersion"] = 2.0
    elif malformation == "missing_version":
        wire.pop("receiptVersion")
    elif malformation == "null_report":
        wire["report"] = None
    elif malformation == "partial_case":
        wire["case"] = {"id": str(uuid4())}
    elif malformation == "wrong_workflow":
        wire["workflowId"] = str(uuid4())
    elif malformation == "legacy_wrong_case":
        wire["report"]["caseId"] = str(uuid4())
    elif malformation == "legacy_missing_report":
        wire.pop("report")
    else:
        wire["patient"] = "untrusted synthetic content"
    with pytest.raises(LegalCoreApiError) as error:
        _request(wire, workflow, operation)
    assert error.value.status_code == 502
    assert error.value.code == "INVALID_LEGAL_CORE_RESPONSE"
    assert "synthetic" not in str(error.value)


def _submit_mode(mode=None, *, schema="dental-case-intake.v2", response_version=2):
    workflow = uuid4()
    requests = []

    def respond(request):
        requests.append(request)
        return httpx2.Response(201, json=_wire(workflow, response_version))

    async def run():
        async with httpx2.AsyncClient(base_url="http://core.test",
                                      transport=httpx2.MockTransport(respond)) as http:
            return await LegalCoreClient(http).submit_workflow(
                workflow, [], 7000000001, intake_schema_version=schema, receipt_version=mode,
            )

    return run, requests


@pytest.mark.parametrize("version", [None, 2])
def test_submission_mode_is_explicit_and_legacy_body_remains_identical(version):
    run, requests = _submit_mode(version)
    result = asyncio.run(run())
    assert result["receiptVersion"] == 2
    expected = {"intakeSchemaVersion": "dental-case-intake.v2", "locale": "ru-RU", "facts": []}
    if version is not None:
        expected["receiptVersion"] = 2
    assert json.loads(requests[0].content) == expected
    assert requests[0].headers["x-telegram-user-id"] == "7000000001"
    assert "x-clinic-id" not in requests[0].headers


@pytest.mark.parametrize("version", [1, 3, True, "2", 2.0])
def test_malformed_submission_mode_is_refused_before_network(version):
    run, requests = _submit_mode(version)
    with pytest.raises(ValueError):
        asyncio.run(run())
    assert requests == []


def test_preparation_mode_cannot_submit_legacy_schema_or_accept_a_legacy_response():
    run, requests = _submit_mode(2, schema="dental-case-intake.v1")
    with pytest.raises(ValueError):
        asyncio.run(run())
    assert requests == []
    run, requests = _submit_mode(2, response_version=1)
    with pytest.raises(LegalCoreApiError) as error:
        asyncio.run(run())
    assert error.value.code == "INVALID_LEGAL_CORE_RESPONSE"
    assert len(requests) == 1
