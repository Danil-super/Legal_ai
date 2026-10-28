"""Only bounded scenario comparison crosses to models; Core receives its source trace."""
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from agent_orchestrator.legal_core_client import LegalCoreClient, LegalCoreEndpoint
from agent_orchestrator.projection import build_projection_from_context
from agent_orchestrator.reasoning import ReasoningResult
from legal_core.analysis_contracts import AnalysisContextResponse
from legal_core.reference_analogy_contracts import (
    ReferenceAnalogyExample, ReferenceAnalogySource, build_reference_analogy_context,
)
from test_legal_core_client import _context


def context():
    text = "Учебный пример: другая клиника разбирала возврат оплаты за услугу."
    source = ReferenceAnalogySource(referenceCaseId=uuid4(), versionId=uuid4(), version=1,
        approvalEventId=uuid4(), scenarioSha256=hashlib.sha256(text.encode()).hexdigest(),
        groupKey="healthcare", evaluationAsOfDate="2026-10-08")
    selected = build_reference_analogy_context([
        ReferenceAnalogyExample(source=source, scenarioText=text)])
    original = _context().model_dump(mode="json", by_alias=True)
    return AnalysisContextResponse.model_validate({**original,
        "referenceExamples": [x.model_dump(mode="json", by_alias=True)
                              for x in selected.examples],
        "referenceExampleTraceSha256": selected.trace_sha256})


def test_projection_strips_private_provenance_but_preserves_exact_scenario_and_date_meaning():
    supplied = context()
    projection = build_projection_from_context(supplied)
    assert len(projection.reference_examples) == 1
    item = projection.reference_examples[0]
    example = supplied.reference_examples[0]
    assert item.analogy_id == "example-1" and item.scenario_text == example.scenario_text
    assert item.evaluation_as_of_date == example.source.evaluation_as_of_date
    assert item.date_basis == "EVALUATION_DATE_NOT_EVENT_DATE"
    payload = projection.model_dump_json(by_alias=True)
    for private in (str(example.source.reference_case_id), str(example.source.version_id),
                    str(example.source.approval_event_id), example.source.scenario_sha256,
                    supplied.reference_example_trace_sha256):
        assert private not in payload
    assert projection.facts == supplied.facts


@pytest.mark.parametrize("fault", ["hash", "body", "missing-trace", "empty", "pii"])
def test_forged_context_cannot_bypass_source_revalidation_before_projection(fault):
    supplied = context()
    if fault == "hash":
        supplied.reference_example_trace_sha256 = "f" * 64
    elif fault == "missing-trace":
        supplied.reference_example_trace_sha256 = None
    elif fault == "empty":
        supplied.reference_examples.clear()
    else:
        supplied.reference_examples[0].__dict__["scenario_text"] = (
            "Учебный контакт patient@example.invalid" if fault == "pii" else
            "Учебный пример: изменённые обстоятельства без изменения хеша.")
    with pytest.raises(ValueError) as caught:
        build_projection_from_context(supplied)
    assert "patient@example.invalid" not in str(caught.value)


def test_submission_forwards_only_reference_trace_and_keeps_old_absent_wire():
    async def run():
        captured = []
        def respond(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={
                "analysisAllowed": False, "riskLevel": "LOW", "escalationRequired": False,
                "report": {"id": str(UUID(int=50)), "caseId": str(_context().case_id),
                    "reportVersion": 2, "reportJson": {"status": "BLOCKED"},
                    "pdfSha256": "f" * 64,
                    "createdAt": datetime(2026, 8, 31, tzinfo=UTC).isoformat()}})
        selected_context = context()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = LegalCoreClient(LegalCoreEndpoint("http://core.test", "k" * 32),
                                     client=http)
            for supplied in (selected_context, _context()):
                await client.submit_reasoning(context=supplied,
                    reasoning=ReasoningResult((), (), (), None), telegram_user_id=123,
                    idempotency_key=uuid4())
        assert captured[0]["expectedReferenceExampleTraceSha256"] == (
            selected_context.reference_example_trace_sha256)
        assert len(captured[0]["expectedReferenceExampleTraceSha256"]) == 64
        assert "expectedReferenceExampleTraceSha256" not in captured[1]
        assert "referenceExamples" not in captured[0] and "scenarioText" not in json.dumps(captured)
    asyncio.run(run())
