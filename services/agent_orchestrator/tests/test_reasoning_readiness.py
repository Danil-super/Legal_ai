import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import UUID

from agent_orchestrator.contracts import (
    CaseProjection,
    ClinicDocumentReadinessItem,
    EvidenceItem,
)
from agent_orchestrator.reasoning import LegalReasoningOrchestrator

FRAGMENT_ID = UUID("00000000-0000-0000-0000-000000000001")
CASE_ID = UUID("00000000-0000-0000-0000-000000000010")


class FakeHermes:
    def __init__(self, *, name: str, response: dict[str, object]) -> None:
        self.endpoint = SimpleNamespace(base_url=f"http://{name}:8642", model=name)
        self.response = response
        self.users: list[str] = []
        self.systems: list[str] = []

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        self.systems.append(system)
        self.users.append(user)
        return self.response


def _projection() -> CaseProjection:
    return CaseProjection(
        caseId=CASE_ID,
        asOfDate=date(2026, 8, 31),
        facts={"SERVICE_TYPE": "Имплантация"},
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
        clinicDocumentReadiness=[
            ClinicDocumentReadinessItem(
                expectationCode="IMPLANT_CONSENT",
                importance="SCENARIO",
                acceptedDocumentTypes=["INFORMED_CONSENT_IMPLANT"],
                reasonCode="IMPLANT_CASE_SPECIALTY_CONSENT",
                status="NOT_AVAILABLE",
                matchedDocumentKeys=[],
                analysisBlocking=False,
            )
        ],
    )


def test_readiness_is_visible_to_researcher_but_never_sent_to_legal_reviewer() -> None:
    async def scenario() -> None:
        researcher = FakeHermes(
            name="researcher-readiness",
            response={
                "claims": [
                    {
                        "claimId": "c1",
                        "kind": "ACTION",
                        "text": "Зафиксировать обращение.",
                        "evidenceFragmentIds": [str(FRAGMENT_ID)],
                        "requiredFactKeys": [],
                    }
                ],
                "internalRecommendations": ["Проверить ИДС на имплантацию."],
                "patientDraft": None,
            },
        )
        reviewer = FakeHermes(
            name="reviewer-readiness",
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

        await orchestrator.reason(_projection())

        assert "clinicDocumentReadiness" in researcher.users[0]
        assert "IMPLANT_CONSENT" in researcher.users[0]
        assert "NOT_AVAILABLE" in researcher.users[0]
        assert "clinicDocumentReadiness" not in reviewer.users[0]
        assert "IMPLANT_CONSENT" not in reviewer.users[0]
        assert "NOT_AVAILABLE" not in reviewer.users[0]
        assert "НЕ перечень юридически обязательных документов" in researcher.systems[0]

    asyncio.run(scenario())
