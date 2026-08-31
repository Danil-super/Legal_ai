from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from agent_orchestrator.main import ServiceDependencies, ServiceSettings, create_app
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
    return TestClient(create_app(settings=settings, dependencies=dependencies)), legal_core, reasoning


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
