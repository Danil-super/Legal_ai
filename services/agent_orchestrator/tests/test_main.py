import asyncio
from datetime import UTC, date, datetime
import json
from uuid import UUID

import pytest
import httpx

from fastapi.testclient import TestClient

from agent_orchestrator.main import ServiceDependencies, ServiceSettings, create_app
from agent_orchestrator.legal_core_client import LegalCoreError
from agent_orchestrator.hermes_client import HermesUnavailable, HermesUnavailableReason
from agent_orchestrator.reasoning import ReasoningResult
from legal_core.analysis_contracts import AnalysisContextResponse, AnalysisSubmissionResponse
from legal_core.api_contracts import LegalFragmentResponse, ReportResponse
from legal_core.verifier import ClaimKind, ProposedClaim, SemanticReview, SemanticVerdict


CASE_ID = UUID("00000000-0000-0000-0000-000000000010")
FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000020")
IDEMPOTENCY_KEY = UUID("00000000-0000-0000-0000-000000000030")
INTERNAL_KEY = "k" * 32


def _context() -> AnalysisContextResponse:
    return AnalysisContextResponse(
        caseId=CASE_ID,
        asOfDate=date(2026, 8, 31),
        facts={"PROBLEM_SUMMARY": "Пациент сообщил о сколе винира."},
        factSnapshotSha256="a" * 64,
        evidenceTraceSha256="b" * 64,
        evidence=[
            LegalFragmentResponse(
                fragmentId=FRAGMENT_ID,
                versionId=UUID("00000000-0000-0000-0000-000000000021"),
                documentId=UUID("00000000-0000-0000-0000-000000000022"),
                article=None,
                part=None,
                point="1",
                structuralPath="point:1",
                fragmentText="Синтетическая проверенная норма.",
                textSha256="c" * 64,
                effectiveFrom=date(2026, 1, 1),
                effectiveTo=None,
                sourceUrl="https://publication.pravo.gov.ru/synthetic",
                rawSha256="d" * 64,
                documentTitle="Синтетический акт",
                issuer="Synthetic authority",
                officialNumber="1",
                versionDate=date(2026, 1, 1),
                publicationDate=date(2026, 1, 1),
            )
        ],
        clinicDocumentContextTraceSha256="f" * 64,
        riskPolicyVersion="dental-risk.v1",
        highDemandThresholdKopecks=10_000_000,
    )


def _reasoning() -> ReasoningResult:
    return ReasoningResult(
        claims=(
            ProposedClaim(
                claim_id="action-1",
                kind=ClaimKind.ACTION,
                text="Зафиксировать обращение.",
                evidence_fragment_ids=(FRAGMENT_ID,),
            ),
        ),
        semantic_reviews=(
            SemanticReview(
                claim_id="action-1",
                verdict=SemanticVerdict.SUPPORTED,
                reviewed_fragment_ids=(FRAGMENT_ID,),
            ),
        ),
        internal_recommendations=(),
        patient_draft=None,
    )


def _submission_response() -> AnalysisSubmissionResponse:
    return AnalysisSubmissionResponse(
        analysisAllowed=True,
        riskLevel="LOW",
        escalationRequired=False,
        report=ReportResponse(
            id=UUID("00000000-0000-0000-0000-000000000040"),
            caseId=CASE_ID,
            reportVersion=2,
            reportJson={"schemaVersion": "dental-case-report.v1"},
            pdfSha256="e" * 64,
            createdAt=datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
        ),
    )


class FakeLegalCore:
    def __init__(self) -> None:
        self.context_calls = 0
        self.submit_calls = 0

    async def get_analysis_context(self, *, case_id: UUID, telegram_user_id: int):
        assert case_id == CASE_ID
        assert telegram_user_id == 123
        self.context_calls += 1
        return _context()

    async def submit_reasoning(
        self,
        *,
        context,
        reasoning,
        telegram_user_id: int,
        idempotency_key: UUID,
    ):
        assert context.case_id == CASE_ID
        assert reasoning.claims[0].claim_id == "action-1"
        assert telegram_user_id == 123
        assert idempotency_key == IDEMPOTENCY_KEY
        self.submit_calls += 1
        return _submission_response()


class FakeReasoning:
    def __init__(self) -> None:
        self.calls = 0

    async def reason(self, projection):
        assert projection.case_id == CASE_ID
        self.calls += 1
        return _reasoning()


def _client():
    legal_core = FakeLegalCore()
    reasoning = FakeReasoning()
    settings = ServiceSettings(
        internal_key=INTERNAL_KEY,
        legal_core_url="http://legal-core:8000",
        hermes_researcher_url="http://hermes-researcher:8642",
        hermes_researcher_key="research-key",
        hermes_researcher_model="researcher",
        hermes_reviewer_url="http://hermes-reviewer:8642",
        hermes_reviewer_key="review-key",
        hermes_reviewer_model="reviewer",
    )
    dependencies = ServiceDependencies(  # type: ignore[arg-type]
        legal_core=legal_core,
        reasoning=reasoning,
    )
    client = TestClient(create_app(settings=settings, dependencies=dependencies))
    return client, legal_core, reasoning


def test_internal_key_is_required_before_analysis() -> None:
    client, legal_core, reasoning = _client()

    response = client.post(
        f"/v1/cases/{CASE_ID}/analyze",
        headers={
            "X-Agent-Internal-Key": "x" * 32,
            "X-Telegram-User-Id": "123",
            "Idempotency-Key": str(IDEMPOTENCY_KEY),
        },
    )

    assert response.status_code == 403
    assert legal_core.context_calls == 0
    assert reasoning.calls == 0


def test_internal_analysis_runs_two_stage_reasoning_and_returns_legal_core_result() -> None:
    client, legal_core, reasoning = _client()

    response = client.post(
        f"/v1/cases/{CASE_ID}/analyze",
        headers={
            "X-Agent-Internal-Key": INTERNAL_KEY,
            "X-Telegram-User-Id": "123",
            "Idempotency-Key": str(IDEMPOTENCY_KEY),
        },
    )

    assert response.status_code == 200
    assert response.json()["analysisAllowed"] is True
    assert response.json()["riskLevel"] == "LOW"
    assert legal_core.context_calls == 1
    assert legal_core.submit_calls == 1
    assert reasoning.calls == 1


def test_completed_case_is_rejected_before_hermes_reasoning() -> None:
    client, legal_core, reasoning = _client()

    async def reject_completed_case(*, case_id: UUID, telegram_user_id: int):
        assert case_id == CASE_ID
        assert telegram_user_id == 123
        raise LegalCoreError(
            "CASE_ANALYSIS_ALREADY_COMPLETED",
            "analysis already completed",
            status_code=409,
        )

    legal_core.get_analysis_context = reject_completed_case
    response = client.post(
        f"/v1/cases/{CASE_ID}/analyze",
        headers={
            "X-Agent-Internal-Key": INTERNAL_KEY,
            "X-Telegram-User-Id": "123",
            "Idempotency-Key": str(IDEMPOTENCY_KEY),
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CASE_ANALYSIS_ALREADY_COMPLETED"
    assert reasoning.calls == 0
    assert legal_core.submit_calls == 0


@pytest.mark.parametrize("reason", ["UNKNOWN", "READ_TIMEOUT", "HTTP_STATUS"])
def test_unavailable_logs_only_closed_reason_without_changing_public_detail(caplog, reason) -> None:
    client, legal_core, reasoning = _client()

    async def unavailable(projection):
        raise HermesUnavailable(
            "SYNTHETIC_PRIVATE_PROVIDER_DETAIL", reason=HermesUnavailableReason(reason),
        )

    reasoning.reason = unavailable
    response = client.post(
        f"/v1/cases/{CASE_ID}/analyze",
        headers={
            "X-Agent-Internal-Key": INTERNAL_KEY,
            "X-Telegram-User-Id": "123",
            "Idempotency-Key": str(IDEMPOTENCY_KEY),
        },
    )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "ANALYSIS_PROVIDER_UNAVAILABLE"}}
    assert legal_core.submit_calls == 0
    records = [r for r in caplog.records if r.name == "agent_orchestrator.main"]
    assert len(records) == 1
    assert json.loads(records[0].getMessage()) == {
        "event": "hermes_unavailable", "reason": reason,
        "entryPoint": "analyze", "requestId": str(IDEMPOTENCY_KEY),
    }
    assert not records[0].exc_info
    assert "SYNTHETIC_PRIVATE_PROVIDER_DETAIL" not in caplog.text
    assert INTERNAL_KEY not in caplog.text


@pytest.mark.parametrize("job_headers", [
    {"X-Analysis-Job-Id": str(IDEMPOTENCY_KEY)},
    {"X-Analysis-Job-Token": str(IDEMPOTENCY_KEY)},
    {"X-Analysis-Job-Id": str(CASE_ID), "X-Analysis-Job-Token": str(IDEMPOTENCY_KEY)},
])
def test_incomplete_or_mismatched_lease_is_not_downgraded_to_unfenced_submission(job_headers):
    client, legal_core, reasoning = _client()
    response = client.post(
        f"/v1/cases/{CASE_ID}/analyze",
        headers={
            "X-Agent-Internal-Key": INTERNAL_KEY,
            "X-Telegram-User-Id": "123",
            "Idempotency-Key": str(IDEMPOTENCY_KEY),
            **job_headers,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ANALYSIS_JOB_LEASE_EXPIRED"
    assert legal_core.context_calls == reasoning.calls == legal_core.submit_calls == 0


def test_runtime_uses_distinct_bounded_streaming_stage_budgets():
    from agent_orchestrator import main
    settings = ServiceSettings(
        internal_key=INTERNAL_KEY, legal_core_url="http://legal-core:8000",
        hermes_researcher_url="http://hermes-researcher:8642",
        hermes_researcher_key="research-key", hermes_researcher_model="researcher",
        hermes_reviewer_url="http://hermes-reviewer:8642",
        hermes_reviewer_key="review-key", hermes_reviewer_model="reviewer",
    )
    dependencies = main.build_dependencies(settings)
    research = dependencies.reasoning._researcher.endpoint
    review = dependencies.reasoning._reviewer.endpoint
    assert (research.timeout_seconds, review.timeout_seconds) == (30, 50)
    assert research.stream_response is review.stream_response is True
    assert (research.model, review.model) == ("researcher", "reviewer")
    assert (research.api_key, review.api_key) == ("research-key", "review-key")
    assert main.ANALYSIS_WALL_TIMEOUT_SECONDS == 115


@pytest.mark.parametrize("stage", ["context", "reasoning", "submission"])
def test_whole_analysis_deadline_cancels_each_stage_and_logs_no_private_detail(
    monkeypatch, caplog, stage,
):
    from agent_orchestrator import main
    monkeypatch.setattr(main, "ANALYSIS_WALL_TIMEOUT_SECONDS", 0.02, raising=False)
    client, legal_core, reasoning = _client()
    cancelled = []

    async def wait_forever(*args, **kwargs):
        try:
            await asyncio.sleep(0.2)
            raise AssertionError("whole deadline did not cancel stage")
        finally:
            cancelled.append(stage)

    if stage == "context":
        legal_core.get_analysis_context = wait_forever
    elif stage == "reasoning":
        reasoning.reason = wait_forever
    else:
        legal_core.submit_reasoning = wait_forever
    response = client.post(
        f"/v1/cases/{CASE_ID}/analyze",
        headers={"X-Agent-Internal-Key": INTERNAL_KEY, "X-Telegram-User-Id": "123",
                 "Idempotency-Key": str(IDEMPOTENCY_KEY)},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "ANALYSIS_PROVIDER_UNAVAILABLE"}}
    assert cancelled == [stage]
    assert legal_core.submit_calls == 0
    records = [r for r in caplog.records if r.name == "agent_orchestrator.main"]
    assert len(records) == 1
    assert json.loads(records[0].getMessage()) == {
        "event": "analysis_deadline_exceeded", "entryPoint": "analyze",
        "requestId": str(IDEMPOTENCY_KEY),
    }
    assert not records[0].exc_info
    assert INTERNAL_KEY not in caplog.text


def test_analysis_wall_budget_is_shared_not_reset_between_stages(monkeypatch):
    from agent_orchestrator import main
    monkeypatch.setattr(main, "ANALYSIS_WALL_TIMEOUT_SECONDS", 0.03, raising=False)
    client, legal_core, reasoning = _client()
    cancelled = []

    async def delayed_context(**kwargs):
        await asyncio.sleep(0.02)
        return _context()

    async def delayed_reasoning(projection):
        try:
            await asyncio.sleep(0.02)
            return _reasoning()
        finally:
            cancelled.append("reasoning")

    legal_core.get_analysis_context = delayed_context
    reasoning.reason = delayed_reasoning
    response = client.post(
        f"/v1/cases/{CASE_ID}/analyze",
        headers={"X-Agent-Internal-Key": INTERNAL_KEY, "X-Telegram-User-Id": "123",
                 "Idempotency-Key": str(IDEMPOTENCY_KEY)},
    )
    assert response.status_code == 503
    assert cancelled == ["reasoning"]
    assert legal_core.submit_calls == 0


def test_whole_deadline_closes_in_progress_hermes_http_stream(monkeypatch):
    from agent_orchestrator import main
    from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint
    monkeypatch.setattr(main, "ANALYSIS_WALL_TIMEOUT_SECONDS", 0.02)
    client, legal_core, reasoning = _client()

    class WaitingStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b": keepalive\n\n"
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    stream = WaitingStream()

    async def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async def wait_for_hermes(projection):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            hermes = HermesClient(HermesEndpoint(
                "http://synthetic-hermes:8642", "synthetic-key", stream_response=True,
            ), client=http)
            await hermes.complete_json(system="fixed system", user="fixed fiction")
        raise AssertionError("cancelled stream cannot become successful reasoning")

    reasoning.reason = wait_for_hermes
    response = client.post(
        f"/v1/cases/{CASE_ID}/analyze",
        headers={"X-Agent-Internal-Key": INTERNAL_KEY, "X-Telegram-User-Id": "123",
                 "Idempotency-Key": str(IDEMPOTENCY_KEY)},
    )
    assert response.status_code == 503
    assert stream.closed
    assert legal_core.submit_calls == 0
