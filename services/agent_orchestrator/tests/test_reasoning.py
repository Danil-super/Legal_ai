import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest
from agent_orchestrator.contracts import (
    CaseProjection,
    ClinicDocumentContextItem,
    EvidenceItem,
)
from agent_orchestrator.hermes_client import HermesProtocolError
from agent_orchestrator.reasoning import LegalReasoningOrchestrator
from legal_core.verifier import SemanticVerdict


FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000099")
CLINIC_FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000777")
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
        self.users: list[str] = []

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        assert system
        assert user
        self.calls += 1
        self.users.append(user)
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


def _projection_with_clinic_context() -> CaseProjection:
    projection = _projection()
    return projection.model_copy(
        update={
            "clinic_document_context": [
                ClinicDocumentContextItem(
                    documentType="WARRANTY_POLICY",
                    documentTitle="Синтетическое положение о гарантиях",
                    versionNo=2,
                    validFrom=date(2026, 7, 1),
                    validTo=None,
                    structuralPath="section:3",
                    text="При сколе конструкции администратор организует осмотр.",
                )
            ]
        }
    )


def _claim_response(fragment_id: UUID = FRAGMENT_ID) -> dict[str, object]:
    return {
        "claims": [
            {
                "claimId": "c1",
                "kind": "LEGAL",
                "text": "Внутренний вывод.",
                "evidenceFragmentIds": [str(fragment_id)],
                "requiredFactKeys": ["FORMAL_CLAIM"],
            }
        ],
        "internalRecommendations": ["Зафиксировать обращение."],
        "patientDraft": "Здравствуйте. Предлагаем провести осмотр.",
    }


def _review_response(fragment_id: UUID = FRAGMENT_ID) -> dict[str, object]:
    return {
        "reviews": [
            {
                "claimId": "c1",
                "verdict": "SUPPORTED",
                "reviewedFragmentIds": [str(fragment_id)],
            }
        ]
    }


def test_two_pass_reasoning_returns_domain_claims_and_reviews() -> None:
    async def scenario() -> None:
        researcher = FakeHermes(name="researcher", response=_claim_response())
        reviewer = FakeHermes(name="reviewer", response=_review_response())
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


def test_researcher_cannot_reference_a_fragment_outside_legal_evidence() -> None:
    async def scenario() -> None:
        researcher = FakeHermes(
            name="researcher",
            response=_claim_response(OTHER_FRAGMENT_ID),
        )
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        orchestrator = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher,
            reviewer=reviewer,
        )

        with pytest.raises(HermesProtocolError, match="outside approved legal evidence"):
            await orchestrator.reason(_projection())
        assert researcher.calls == 1
        assert reviewer.calls == 0

    asyncio.run(scenario())


def test_reviewer_cannot_reference_a_fragment_outside_the_claim() -> None:
    async def scenario() -> None:
        researcher = FakeHermes(name="researcher", response=_claim_response())
        reviewer = FakeHermes(
            name="reviewer",
            response=_review_response(OTHER_FRAGMENT_ID),
        )
        orchestrator = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher,
            reviewer=reviewer,
        )

        with pytest.raises(HermesProtocolError, match="outside approved legal evidence"):
            await orchestrator.reason(_projection())
        assert researcher.calls == 1
        assert reviewer.calls == 1

    asyncio.run(scenario())


def test_clinic_document_context_is_visible_to_researcher_but_hidden_from_reviewer() -> None:
    async def scenario() -> None:
        researcher = FakeHermes(name="researcher", response=_claim_response())
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        orchestrator = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher,
            reviewer=reviewer,
        )

        await orchestrator.reason(_projection_with_clinic_context())

        assert "Синтетическое положение о гарантиях" in researcher.users[0]
        assert "При сколе конструкции" in researcher.users[0]
        assert "Синтетическое положение о гарантиях" not in reviewer.users[0]
        assert "При сколе конструкции" not in reviewer.users[0]

    asyncio.run(scenario())


def test_researcher_cannot_promote_a_clinic_document_id_to_legal_evidence() -> None:
    async def scenario() -> None:
        researcher = FakeHermes(
            name="researcher",
            response=_claim_response(CLINIC_FRAGMENT_ID),
        )
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        orchestrator = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher,
            reviewer=reviewer,
        )

        with pytest.raises(HermesProtocolError, match="outside approved legal evidence"):
            await orchestrator.reason(_projection_with_clinic_context())
        assert researcher.calls == 1
        assert reviewer.calls == 0

    asyncio.run(scenario())
