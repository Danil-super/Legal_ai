import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest
from agent_orchestrator.contracts import CaseProjection, EvidenceItem
from agent_orchestrator.reasoning import LegalReasoningOrchestrator
from legal_core.verifier import SemanticVerdict


FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000001")
CASE_ID = UUID("00000000-0000-0000-0000-000000000010")


class FakeHermes:
    def __init__(
        self,
        *,
        name: str,
        response: dict[str, object],
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.endpoint = SimpleNamespace(
            base_url=base_url or f"http://{name}:8642",
            model=model or name,
        )
        self.response = response
        self.calls = 0

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        assert system
        assert user
        self.calls += 1
        return self.response


def _projection(*, summary: str = "Пациент сообщил о сколе винира") -> CaseProjection:
    return CaseProjection(
        caseId=CASE_ID,
        asOfDate=date(2026, 8, 31),
        facts={"PROBLEM_SUMMARY": summary, "FORMAL_CLAIM": "NO"},
        evidence=[
            EvidenceItem(
                fragmentId=FRAGMENT_ID,
                documentTitle="Синтетический акт",
                officialNumber="1",
                structuralPath="point:1",
                text="Синтетическая проверенная норма.",
                effectiveFrom=date(2026, 1, 1),
                effectiveTo=None,
                sourceUrl="https://publication.pravo.gov.ru/synthetic",
            )
        ],
    )


def test_two_pass_reasoning_returns_domain_claims_and_reviews() -> None:
    async def scenario() -> None:
        researcher = FakeHermes(
            name="researcher",
            response={
                "claims": [
                    {
                        "claimId": "c1",
                        "kind": "LEGAL",
                        "text": "Внутренний вывод.",
                        "evidenceFragmentIds": [str(FRAGMENT_ID)],
                        "requiredFactKeys": ["FORMAL_CLAIM"],
                    }
                ],
                "internalRecommendations": ["Зафиксировать обращение."],
                "patientDraft": "Здравствуйте. Предлагаем провести осмотр.",
            },
        )
        reviewer = FakeHermes(
            name="reviewer",
            response={
                "reviews": [
                    {
                        "claimId": "c1",
                        "verdict": "SUPPORTED",
                        "reviewedFragmentIds": [str(FRAGMENT_ID)],
                    }
                ]
            },
        )
        orchestrator = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher,
            reviewer=reviewer,
        )

        result = await orchestrator.reason(_projection())

        assert result.claims[0].claim_id == "c1"
        assert result.semantic_reviews[0].verdict is SemanticVerdict.SUPPORTED
        assert result.internal_recommendations == ("Зафиксировать обращение.",)
        assert result.patient_draft == "Здравствуйте. Предлагаем провести осмотр."
        assert researcher.calls == 1
        assert reviewer.calls == 1

    asyncio.run(scenario())


def test_orchestrator_rejects_same_researcher_and_reviewer_origin() -> None:
    researcher = FakeHermes(
        name="researcher",
        base_url="http://same-hermes:8642",
        model="researcher",
        response={},
    )
    reviewer = FakeHermes(
        name="reviewer",
        base_url="http://same-hermes:8642",
        model="reviewer",
        response={},
    )
    with pytest.raises(ValueError, match="distinct Hermes endpoint origins"):
        LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher,
            reviewer=reviewer,
        )


def test_orchestrator_rejects_obvious_identifier_before_provider_call() -> None:
    async def scenario() -> None:
        researcher = FakeHermes(name="researcher", response={})
        reviewer = FakeHermes(name="reviewer", response={})
        orchestrator = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher,
            reviewer=reviewer,
        )

        with pytest.raises(ValueError, match="direct identifier"):
            await orchestrator.reason(_projection(summary="Телефон пациента +7 999 123-45-67"))
        assert researcher.calls == 0
        assert reviewer.calls == 0

    asyncio.run(scenario())
