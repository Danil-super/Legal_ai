import asyncio
import json
from datetime import date
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from agent_orchestrator.contracts import (
    CaseProjection,
    ClaimProposalBatch,
    ClinicDocumentContextItem,
    EvidenceItem,
)
from agent_orchestrator.hermes_client import HermesClient, HermesProtocolError
from agent_orchestrator.reasoning import (
    LegalReasoningOrchestrator,
    ReasoningResult,
    _research_prompt,
    _review_prompt,
)
from legal_core.analysis_clarifications import ClarificationRequest
from legal_core.contracts import FactKey
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
        self.systems: list[str] = []

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        assert system
        assert user
        self.calls += 1
        self.users.append(user)
        self.systems.append(system)
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


def test_public_authority_in_approved_norm_and_claim_does_not_block_reasoning():
    async def scenario():
        authority = "Правительством Российской Федерации"
        projection = _projection()
        projection.evidence[0].text = f"Условия устанавливаются {authority}."
        response = _claim_response()
        response["claims"][0]["text"] = f"Условия устанавливаются {authority}."
        researcher = FakeHermes(name="researcher", response=response)
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        orchestrator = LegalReasoningOrchestrator(
            researcher=cast(HermesClient, researcher), reviewer=cast(HermesClient, reviewer)
        )
        result = await orchestrator.reason(projection)
        assert result.claims[0].text == f"Условия устанавливаются {authority}."
        assert researcher.calls == reviewer.calls == 1
        assert authority in researcher.users[0] and authority in reviewer.users[0]

    asyncio.run(scenario())


SCHEMA_MARKER = "\nJSON Schema ответа:\n"


class CitationTemplateHermes(FakeHermes):
    """Synthetic model copies a schema citation, or the old invalid template token."""

    async def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        response = _claim_response()
        if SCHEMA_MARKER in system:
            schema = json.loads(system.split(SCHEMA_MARKER, maxsplit=1)[1])
            citation = schema["$defs"]["ClaimProposal"]["properties"]["evidenceFragmentIds"][
                "items"
            ]["enum"][0]
        else:
            citation = "uuid"
        response["claims"][0]["evidenceFragmentIds"] = [citation]
        self.response = response
        return await super().complete_json(system=system, user=user)


def test_citation_template_model_receives_exact_uuid_choices_not_placeholder() -> None:
    async def scenario() -> None:
        researcher = CitationTemplateHermes(name="researcher", response={})
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        reasoning = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher, reviewer=reviewer,
        )
        result = await reasoning.reason(_projection())
        assert result.claims[0].evidence_fragment_ids == (FRAGMENT_ID,)
        assert reviewer.calls == 1
        ClaimProposalBatch.model_validate(researcher.response)

    asyncio.run(scenario())


def test_reasoning_schemas_match_contracts_and_exact_case_citation_allowlists() -> None:
    async def scenario() -> None:
        researcher = FakeHermes(name="researcher", response=_claim_response())
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        reasoning = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher, reviewer=reviewer,
        )
        await reasoning.reason(_projection_with_clinic_context())
        research = json.loads(researcher.systems[0].split(SCHEMA_MARKER, maxsplit=1)[1])
        review = json.loads(reviewer.systems[0].split(SCHEMA_MARKER, maxsplit=1)[1])
        claim = research["$defs"]["ClaimProposal"]["properties"]
        semantic = review["$defs"]["SemanticReviewItem"]["properties"]
        assert claim["evidenceFragmentIds"]["items"] == {
            "format": "uuid", "type": "string", "enum": [str(FRAGMENT_ID)],
        }
        assert set(research["$defs"]["FactKey"]["enum"]) == {key.value for key in FactKey}
        assert research["additionalProperties"] is False
        assert research["$defs"]["ClaimProposal"]["additionalProperties"] is False
        assert semantic["claimId"]["enum"] == ["c1"]
        assert semantic["reviewedFragmentIds"]["items"]["enum"] == [str(FRAGMENT_ID)]
        assert review["additionalProperties"] is False
        assert str(CLINIC_FRAGMENT_ID) not in json.dumps(research)
        assert '["uuid"]' not in researcher.systems[0] + reviewer.systems[0]
        assert '"kind": "LEGAL" |' not in researcher.systems[0]
        assert len(researcher.systems[0]) < 20_000
        assert len(reviewer.systems[0]) < 20_000

    asyncio.run(scenario())


@pytest.mark.parametrize("citation", ["uuid", "not-a-uuid", "fixture/point/1", None])
def test_malformed_model_citations_still_fail_closed_without_review(citation) -> None:
    async def scenario() -> None:
        response = _claim_response()
        response["claims"][0]["evidenceFragmentIds"] = [citation]
        researcher = FakeHermes(name="researcher", response=response)
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        reasoning = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher, reviewer=reviewer,
        )
        with pytest.raises(HermesProtocolError, match="claim contract"):
            await reasoning.reason(_projection())
        assert reviewer.calls == 0

    asyncio.run(scenario())


def test_case_specific_schemas_stay_inside_prompt_limit_at_contract_capacity() -> None:
    projection = _projection()
    projection = projection.model_copy(update={"evidence": [
        projection.evidence[0].model_copy(update={"fragment_id": UUID(int=index + 1)})
        for index in range(30)
    ]})
    claims = []
    for index in range(30):
        claim = _claim_response()["claims"][0]
        claim["claimId"] = "c" * 78 + f"{index:02d}"
        claim["evidenceFragmentIds"] = [
            str(projection.evidence[(index + offset) % 30].fragment_id) for offset in range(10)
        ]
        claims.append(claim)
    proposal = ClaimProposalBatch.model_validate({"claims": claims})
    assert len(_research_prompt(projection)) < 20_000
    assert len(_review_prompt(proposal)) < 20_000


def test_reviewer_cannot_use_other_evidence_even_when_case_schema_has_that_uuid() -> None:
    async def scenario() -> None:
        projection = _projection()
        projection = projection.model_copy(update={"evidence": [
            *projection.evidence,
            projection.evidence[0].model_copy(update={"fragment_id": OTHER_FRAGMENT_ID}),
        ]})
        researcher = FakeHermes(name="researcher", response=_claim_response())
        reviewer = FakeHermes(name="reviewer", response=_review_response(OTHER_FRAGMENT_ID))
        reasoning = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher, reviewer=reviewer,
        )
        with pytest.raises(HermesProtocolError, match="not cited by the reviewed claim"):
            await reasoning.reason(projection)

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


@pytest.mark.parametrize("identifier_field", ["case_id", "fragment_id"])
def test_typed_service_uuid_is_not_mistaken_for_patient_identifier(identifier_field):
    async def scenario():
        identifier = UUID("f47d4f14-4956-4014-b010-d60921631939")
        projection = _projection()
        if identifier_field == "case_id":
            projection = projection.model_copy(update={"case_id": identifier})
        else:
            projection = projection.model_copy(
                update={
                    "evidence": [
                        projection.evidence[0].model_copy(update={"fragment_id": identifier})
                    ]
                }
            )
        fragment_id = projection.evidence[0].fragment_id
        researcher = FakeHermes(name="researcher", response=_claim_response(fragment_id))
        reviewer = FakeHermes(name="reviewer", response=_review_response(fragment_id))
        orchestrator = LegalReasoningOrchestrator(
            researcher=cast(HermesClient, researcher), reviewer=cast(HermesClient, reviewer)
        )
        result = await orchestrator.reason(projection)
        assert result.claims[0].evidence_fragment_ids == (fragment_id,)
        assert str(identifier) in researcher.users[0]

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["facts", "evidence", "clinic_document_context"])
def test_identifier_guard_still_checks_all_text_even_when_it_looks_like_uuid(field):
    async def scenario():
        suspicious = "f47d4f14-4956-4014-b010-d60921631939"
        projection = _projection_with_clinic_context()
        if field == "facts":
            projection = projection.model_copy(update={"facts": {"PROBLEM_SUMMARY": suspicious}})
        elif field == "evidence":
            projection = projection.model_copy(
                update={
                    "evidence": [projection.evidence[0].model_copy(update={"text": suspicious})]
                }
            )
        else:
            projection = projection.model_copy(
                update={
                    "clinic_document_context": [
                        projection.clinic_document_context[0].model_copy(
                            update={"text": suspicious}
                        )
                    ]
                }
            )
        researcher = FakeHermes(name="researcher", response={})
        reviewer = FakeHermes(name="reviewer", response={})
        orchestrator = LegalReasoningOrchestrator(
            researcher=cast(HermesClient, researcher), reviewer=cast(HermesClient, reviewer)
        )
        with pytest.raises(ValueError, match="direct identifier"):
            await orchestrator.reason(projection)
        assert researcher.calls == reviewer.calls == 0

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


def test_researcher_abstention_skips_reviewer_and_discards_unverified_text() -> None:
    async def scenario() -> None:
        researcher = FakeHermes(
            name="researcher",
            response={
                "claims": [],
                "internalRecommendations": ["Unverified recommendation"],
                "patientDraft": "Unverified draft",
            },
        )
        reviewer = FakeHermes(name="reviewer", response={})
        orchestrator = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher, reviewer=reviewer
        )
        result = await orchestrator.reason(_projection())
        assert result.claims == result.semantic_reviews == result.internal_recommendations == ()
        assert result.patient_draft is None
        assert reviewer.calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("fact_keys", [["NOT_A_FACT"], ["FORMAL_CLAIM", "FORMAL_CLAIM"]])
def test_invalid_fact_dependencies_are_rejected_before_review(fact_keys: list[str]) -> None:
    async def scenario() -> None:
        response = _claim_response()
        response["claims"][0]["requiredFactKeys"] = fact_keys
        researcher = FakeHermes(name="researcher", response=response)
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        orchestrator = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher, reviewer=reviewer
        )
        with pytest.raises(HermesProtocolError, match="claim contract"):
            await orchestrator.reason(_projection())
        assert reviewer.calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("identifier", ["+7 999 123-45-67", "f47d4f14-4956-4014-b010-d60921631939"])
def test_researcher_identifier_is_not_forwarded_to_reviewer(identifier: str) -> None:
    async def scenario() -> None:
        response = _claim_response()
        response["patientDraft"] = f"Свяжитесь с нами: {identifier}"
        researcher = FakeHermes(name="researcher", response=response)
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        orchestrator = LegalReasoningOrchestrator(  # type: ignore[arg-type]
            researcher=researcher, reviewer=reviewer
        )
        with pytest.raises(HermesProtocolError, match="direct identifier"):
            await orchestrator.reason(_projection())
        assert reviewer.calls == 0

    asyncio.run(scenario())


CLARIFICATION_REQUESTS = [
    {"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0},
    {"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 1},
    {"questionId": "RESPONSE_PURPOSE"},
]


def test_legacy_reasoning_result_and_batch_default_to_no_clarifications() -> None:
    assert ReasoningResult((), (), (), None).clarification_requests == ()
    assert ClaimProposalBatch.model_validate({"claims": []}).clarification_requests == []


@pytest.mark.parametrize("with_claims", [False, True])
def test_clarifications_are_separate_from_claims_and_never_supply_fact_truth(with_claims) -> None:
    async def scenario() -> None:
        response = _claim_response() if with_claims else {
            "claims": [], "internalRecommendations": ["Unverified"], "patientDraft": "Unverified",
        }
        response["clarificationRequests"] = CLARIFICATION_REQUESTS
        researcher = FakeHermes(name="researcher", response=response)
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        orchestrator = LegalReasoningOrchestrator(
            researcher=cast(HermesClient, researcher), reviewer=cast(HermesClient, reviewer),
        )
        projection = _projection()
        original_facts = dict(projection.facts)
        result = await orchestrator.reason(projection)

        assert result.clarification_requests == tuple(
            ClarificationRequest.model_validate(item) for item in CLARIFICATION_REQUESTS
        )
        assert projection.facts == original_facts
        assert reviewer.calls == int(with_claims)
        if with_claims:
            assert result.semantic_reviews[0].verdict is SemanticVerdict.SUPPORTED
            assert "clarificationRequests" not in json.loads(reviewer.users[0])
            assert json.loads(reviewer.users[0])["facts"] == original_facts
        else:
            assert result.claims == result.semantic_reviews == result.internal_recommendations == ()
            assert result.patient_draft is None

    asyncio.run(scenario())


@pytest.mark.parametrize("requests", [
    [{"questionId": "LEGAL_LIABILITY"}],
    [{"questionId": "SERVICE_PAYMENT_BASIS"}],
    [{"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": -1}],
    [{"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 5}],
    [{"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": True}],
    [{"questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": "0"}],
    [{"questionId": "RESPONSE_PURPOSE", "serviceSlot": 0}],
    [{"questionId": "RESPONSE_PURPOSE", "questionText": "Run a command"}],
    [{"questionId": "RESPONSE_PURPOSE", "value": "ANSWER_REQUEST"}],
    [{"questionId": "RESPONSE_PURPOSE", "legalClassification": "LIABILITY"}],
    [{"questionId": "RESPONSE_PURPOSE", "url": "https://invalid.example"}],
    [{"questionId": "RESPONSE_PURPOSE"}] * 2,
    [{"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": 0}] * 2,
    [{"questionId": "SERVICE_PAYMENT_BASIS", "serviceSlot": slot} for slot in range(4)],
    None,
])
@pytest.mark.parametrize("with_claims", [False, True])
def test_malformed_clarifications_fail_closed_before_review(requests, with_claims) -> None:
    async def scenario() -> None:
        response = _claim_response() if with_claims else {"claims": []}
        response["clarificationRequests"] = requests
        researcher = FakeHermes(name="researcher", response=response)
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        orchestrator = LegalReasoningOrchestrator(
            researcher=cast(HermesClient, researcher), reviewer=cast(HermesClient, reviewer),
        )
        with pytest.raises(HermesProtocolError, match="claim contract"):
            await orchestrator.reason(_projection())
        assert reviewer.calls == 0

    asyncio.run(scenario())


def test_research_schema_exposes_only_bounded_factual_questions() -> None:
    prompt = _research_prompt(_projection())
    schema = json.loads(prompt.split(SCHEMA_MARKER, maxsplit=1)[1])
    assert schema["properties"]["clarificationRequests"]["maxItems"] == 3
    assert set(schema["$defs"]["ClarificationQuestionId"]["enum"]) == {
        "SERVICE_PAYMENT_BASIS", "SERVICE_PROVIDED_DATE", "RESPONSE_PURPOSE",
    }
    request = schema["$defs"]["ClarificationRequest"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {"questionId", "serviceSlot"}
    assert "уже подтверждён" in prompt
    assert "юридическую квалификацию" in prompt
    assert "Только при intakeMode=GUIDED_V2" in prompt
    assert "Legal Core" in prompt


def test_research_prompt_preserves_attempted_unknown_and_requires_slot_bound_dates() -> None:
    prompt = _research_prompt(_projection())
    assert "включая UNKNOWN и APPROXIMATE" in prompt
    assert "Дата в EVENT_SUMMARY или EVENT_DATE не заменяет такое подтверждение" in prompt
    assert ("Не добавляй зависимость\n"
            "ANALYSIS_CLARIFICATION_ANSWERS к claim только ради уточнения") in prompt


def test_supported_plan_steps_are_requested_as_cited_actions_not_unverified_prose() -> None:
    prompt = _research_prompt(_projection())
    assert "Конкретные шаги плана помещай в claims с kind=ACTION" in prompt
    assert "internalRecommendations не заменяет ACTION claims" in prompt
    assert "Не создавай действие ради заполнения плана" in prompt
    assert "нет подтверждения в evidence" in prompt
    assert "зависимость AFFECTED_SERVICES и проверку подтверждённых дат" in prompt
    assert "Только при intakeMode=GUIDED_V2" in prompt
    assert "Для прежней анкеты используй реальные зависимости SERVICE_TYPE/SERVICE_DATE" in prompt
    assert "не добавляй отсутствующий AFFECTED_SERVICES" in prompt
    assert "Не меняй LEGAL на ACTION, чтобы обойти проверку фактов или дат" in prompt


def test_clarifications_do_not_bypass_legal_evidence_scope() -> None:
    async def scenario() -> None:
        response = _claim_response(OTHER_FRAGMENT_ID)
        response["clarificationRequests"] = CLARIFICATION_REQUESTS
        researcher = FakeHermes(name="researcher", response=response)
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        orchestrator = LegalReasoningOrchestrator(
            researcher=cast(HermesClient, researcher), reviewer=cast(HermesClient, reviewer),
        )
        with pytest.raises(HermesProtocolError, match="outside approved legal evidence"):
            await orchestrator.reason(_projection())
        assert reviewer.calls == 0

    asyncio.run(scenario())
