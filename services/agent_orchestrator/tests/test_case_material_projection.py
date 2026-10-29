"""Private attachment provenance stays bound to Core; models get only safe excerpts."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from agent_orchestrator.legal_core_client import (
    LegalCoreClient, LegalCoreEndpoint, LegalCoreProtocolError,
)
from agent_orchestrator.projection import build_projection_from_context
from agent_orchestrator.reasoning import ReasoningResult
from legal_core.analysis_contracts import AnalysisContextResponse
from legal_core.case_material_context_contracts import (
    CaseMaterialContextFragment, case_material_context_trace_sha256,
)
from test_legal_core_client import _context
from test_preparation_transport import preparation_wire

TEXT = "Синтетическая памятка: согласовать осмотр со специалистом клиники."


def context(text=TEXT):
    fragment = CaseMaterialContextFragment(
        materialId=UUID(int=2001), fragmentId=UUID(int=2002), displayName="Материал 1",
        mimeType="text/plain", rawSha256="8f" * 32,
        textSha256=hashlib.sha256(text.encode()).hexdigest(),
        structuralPath="text/fragment/1", text=text, extractionLimitations=[],
    )
    wire = _context().model_dump(mode="json", by_alias=True)
    return AnalysisContextResponse.model_validate(wire | {
        "caseMaterialContext": [fragment.model_dump(mode="json", by_alias=True)],
        "caseMaterialContextTraceSha256": case_material_context_trace_sha256([fragment]),
    })


def test_projection_redacts_text_and_strips_all_private_case_file_provenance():
    supplied = context("Синтетический контакт demo@example.invalid; предложен осмотр.")
    projection = build_projection_from_context(supplied)
    wire = projection.model_dump(mode="json", by_alias=True)
    item = wire["caseMaterialContext"][0]
    assert item == {
        "contextKind": "CASE_MATERIAL_CONTEXT", "displayName": "Материал 1",
        "structuralPath": "text/fragment/1",
        "text": "Синтетический контакт [EMAIL]; предложен осмотр.",
        "extractionLimitations": [],
    }
    encoded = json.dumps(wire, ensure_ascii=False)
    fragment = supplied.case_material_context[0]
    for private in (str(fragment.material_id), str(fragment.fragment_id), fragment.raw_sha256,
                    fragment.text_sha256, supplied.case_material_context_trace_sha256,
                    "demo@example.invalid"):
        assert private not in encoded
    assert projection.facts == supplied.facts


@pytest.mark.parametrize("tamper", ["trace", "missing-trace", "missing-items", "empty-items",
                                  "text", "mime", "oversize"])
def test_attachment_source_is_revalidated_before_projection_even_for_mutated_models(tamper):
    supplied = context()
    if tamper == "trace":
        supplied.case_material_context_trace_sha256 = "0" * 64
    elif tamper == "missing-trace":
        supplied.case_material_context_trace_sha256 = None
    elif tamper == "missing-items":
        supplied.case_material_context = None
    elif tamper == "empty-items":
        supplied.case_material_context = []
    else:
        supplied.case_material_context[0].__dict__[
            "mime_type" if tamper == "mime" else "text"
        ] = "application/x-private" if tamper == "mime" else (
            "x" * 1201 if tamper == "oversize" else "Изменено без обновления хеша.")
    with pytest.raises(ValueError, match="case material"):
        build_projection_from_context(supplied)


def response(supplied, version, trace):
    if version == 2:
        wire = preparation_wire(supplied, "LIMITED")
        wire["reasonCode"] = "EVIDENCE_NOT_VERIFIED"
        if trace is not None:
            wire["source"]["context"]["caseMaterialContextTraceSha256"] = trace
        return wire
    report = {"status": "BLOCKED"}
    if trace is not None:
        report["caseMaterialContextTraceSha256"] = trace
    return {"analysisAllowed": False, "riskLevel": "LOW", "escalationRequired": False,
            "report": {"id": str(UUID(int=50)), "caseId": str(supplied.case_id),
                       "reportVersion": 2, "reportJson": report, "pdfSha256": "f" * 64,
                       "createdAt": datetime(2026, 8, 31, tzinfo=UTC).isoformat()}}


@pytest.mark.parametrize("version", [1, 2])
def test_private_submission_forwards_the_exact_case_file_trace_and_preserves_absent_wire(version):
    async def run():
        captured = []
        supplied = context()

        def handle(request):
            body = json.loads(request.content)
            captured.append(body)
            return httpx.Response(200, json=response(supplied, version,
                supplied.case_material_context_trace_sha256 if len(captured) == 1 else None))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core.test", "k" * 32), client=http)
            for current in (supplied, _context()):
                await client.submit_reasoning(
                    context=current, reasoning=ReasoningResult((), (), (), None),
                    telegram_user_id=123, idempotency_key=uuid4(), result_version=version)
        assert captured[0]["expectedCaseMaterialContextTraceSha256"] == (
            supplied.case_material_context_trace_sha256)
        assert "expectedCaseMaterialContextTraceSha256" not in captured[1]
        for body in captured:
            assert "caseMaterialContext" not in body and TEXT not in json.dumps(body)
            assert "rawSha256" not in body and "materialId" not in body

    asyncio.run(run())


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("tamper", ["lost", "changed", "unexpected"])
def test_private_response_cannot_drop_swap_or_introduce_a_case_material_trace(version, tamper):
    supplied = _context() if tamper == "unexpected" else context()
    trace = None if tamper == "lost" else "0" * 64

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response(supplied, version, trace)),
        )) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core.test", "k" * 32), client=http)
            with pytest.raises(LegalCoreProtocolError, match="context mismatch"):
                await client.submit_reasoning(context=supplied,
                    reasoning=ReasoningResult((), (), (), None), telegram_user_id=123,
                    idempotency_key=uuid4(), result_version=version)

    asyncio.run(run())
