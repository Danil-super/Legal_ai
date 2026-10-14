import asyncio
import json
from datetime import UTC, date, datetime
from uuid import UUID

import httpx
import pytest

from agent_orchestrator.legal_core_client import LegalCoreClient, LegalCoreEndpoint
from agent_orchestrator.reasoning import ReasoningResult
from legal_core.analysis_clarifications import ClarificationRequest
from legal_core.analysis_contracts import AnalysisContextResponse, AnalysisSubmissionRequest
from legal_core.api_contracts import LegalFragmentResponse


CASE_ID = UUID(int=10)
IDEMPOTENCY_KEY = UUID(int=30)


def _context() -> AnalysisContextResponse:
    return AnalysisContextResponse(
        caseId=CASE_ID, asOfDate=date(2026, 8, 31),
        facts={"PROBLEM_SUMMARY": "Synthetic complaint"},
        factSnapshotSha256="a" * 64, evidenceTraceSha256="b" * 64,
        clinicDocumentContextTraceSha256="c" * 64,
        riskPolicyVersion="dental-risk.v4", highDemandThresholdKopecks=1_000_000,
        evidence=[LegalFragmentResponse(
            fragmentId=UUID(int=1), versionId=UUID(int=2), documentId=UUID(int=3),
            article=None, part=None, point="1", structuralPath="point:1",
            fragmentText="Synthetic approved norm", textSha256="d" * 64,
            effectiveFrom=date(2026, 1, 1), effectiveTo=None,
            sourceUrl="https://publication.pravo.gov.ru/synthetic", rawSha256="e" * 64,
            documentTitle="Synthetic act", issuer="Synthetic authority", officialNumber="1",
            versionDate=date(2026, 1, 1), publicationDate=date(2026, 1, 1),
        )],
    )


@pytest.mark.parametrize("with_requests", [False, True])
def test_submission_preserves_snapshots_scope_and_optional_clarifications(with_requests) -> None:
    async def scenario() -> None:
        received: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == f"/v1/cases/{CASE_ID}/analysis-submissions"
            assert request.headers["X-Telegram-User-Id"] == "123"
            assert request.headers["Idempotency-Key"] == str(IDEMPOTENCY_KEY)
            assert request.headers["X-Analysis-Job-Id"] == str(UUID(int=40))
            assert request.headers["X-Analysis-Job-Token"] == str(UUID(int=41))
            payload = json.loads(request.content)
            AnalysisSubmissionRequest.model_validate(payload)
            received.append(payload)
            return httpx.Response(200, json={
                "analysisAllowed": False, "riskLevel": "LOW", "escalationRequired": False,
                "report": {
                    "id": str(UUID(int=50)), "caseId": str(CASE_ID), "reportVersion": 2,
                    "reportJson": {"status": "BLOCKED"}, "pdfSha256": "f" * 64,
                    "createdAt": datetime(2026, 8, 31, tzinfo=UTC).isoformat(),
                },
            })

        clarification_requests = (
            ClarificationRequest(questionId="SERVICE_PAYMENT_BASIS", serviceSlot=0),
            ClarificationRequest(questionId="RESPONSE_PURPOSE"),
        ) if with_requests else ()
        reasoning = ReasoningResult((), (), (), None, clarification_requests=clarification_requests)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            core = LegalCoreClient(
                LegalCoreEndpoint(base_url="http://legal-core:8000", internal_key="k" * 32),
                client=client,
            )
            result = await core.submit_reasoning(
                context=_context(), reasoning=reasoning, telegram_user_id=123,
                idempotency_key=IDEMPOTENCY_KEY, job_id=UUID(int=40), job_token=UUID(int=41),
            )
        assert result.analysis_allowed is False
        payload = received[0]
        assert payload["expectedFactSnapshotSha256"] == "a" * 64
        assert payload["expectedEvidenceTraceSha256"] == "b" * 64
        assert payload["expectedClinicDocumentContextTraceSha256"] == "c" * 64
        assert payload["expectedRiskPolicyVersion"] == "dental-risk.v4"
        assert payload["claims"] == payload["semanticReviews"] == []
        assert "clinicId" not in payload and "facts" not in payload
        if with_requests:
            assert payload["clarificationRequests"] == [
                {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
                {"questionId": "RESPONSE_PURPOSE", "serviceSlot": None},
            ]
        else:
            assert "clarificationRequests" not in payload

    asyncio.run(scenario())
