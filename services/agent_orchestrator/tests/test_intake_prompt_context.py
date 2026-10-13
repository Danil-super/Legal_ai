"""Prompt metadata must distinguish intake versions without weakening verifier gates."""

import asyncio
import json
from datetime import date
from typing import cast
from uuid import UUID

import pytest

from agent_orchestrator.hermes_client import HermesClient
from agent_orchestrator.reasoning import LegalReasoningOrchestrator, _research_prompt
from legal_core.contracts import FactKey
from legal_core.legal_retrieval import ApprovedLegalFragment
from legal_core.verifier import VerificationResult, verify_claims
from test_reasoning import FakeHermes, _claim_response, _projection, _review_response

CONTEXT_MARKER = "\nJSON контекста анкеты:\n"
SCHEMA_MARKER = "\nJSON Schema ответа:\n"


def context_for(facts):
    projection = _projection().model_copy(update={"facts": facts})
    prompt = _research_prompt(projection)
    assert CONTEXT_MARKER in prompt
    encoded = prompt.split(CONTEXT_MARKER, 1)[1].split(SCHEMA_MARKER, 1)[0]
    return prompt, json.loads(encoded)


def test_guided_context_does_not_suggest_missing_legacy_fields():
    facts = {
        "INTAKE_VERSION": "GUIDED_V2",
        "EVENT_DATE": {"precision": "UNKNOWN", "date": None},
        "EVENT_SUMMARY": "Синтетическое ожидание приёма.",
        "SITUATION_AREAS": ["SERVICE"],
    }
    prompt, context = context_for(facts)
    assert context == {"intakeMode": "GUIDED_V2", "presentFactKeys": sorted(facts)}
    schema = json.loads(prompt.split(SCHEMA_MARKER, 1)[1])
    # A genuinely required missing dependency must remain representable and block analysis.
    assert set(schema["$defs"]["FactKey"]["enum"]) == {key.value for key in FactKey}
    assert facts["EVENT_DATE"]["precision"] == "UNKNOWN"


@pytest.mark.parametrize("mode", [None, "GUIDED_V1", "UNSUPPORTED"])
def test_non_guided_cases_are_not_reclassified_as_guided(mode):
    facts = {
        "SERVICE_TYPE": "Синтетическая услуга",
        "SERVICE_DATE": {"date": "2026-10-13", "precision": "EXACT"},
    }
    if mode is not None:
        facts["INTAKE_VERSION"] = mode
    _, context = context_for(facts)
    assert context["intakeMode"] == "UNSPECIFIED"
    assert context["presentFactKeys"] == sorted(facts)


def test_untrusted_fact_names_and_values_never_enter_trusted_prompt_metadata():
    facts = {
        "INTAKE_VERSION": "GUIDED_V2",
        "EVENT_SUMMARY": "untrusted-value-marker",
        "untrusted-key-marker": "data-not-instruction",
    }
    prompt, context = context_for(facts)
    assert context["presentFactKeys"] == ["EVENT_SUMMARY", "INTAKE_VERSION"]
    assert "untrusted-key-marker" not in prompt
    assert "untrusted-value-marker" not in prompt


def test_guided_guidance_preserves_real_dependencies_and_separate_date_meanings():
    prompt, _ = context_for({"INTAKE_VERSION": "GUIDED_V2"})
    for instruction in (
        "presentFactKeys НЕ означает, что значение известно или подтверждено",
        "При intakeMode=GUIDED_V2 описание находится в EVENT_SUMMARY",
        "дата события — в EVENT_DATE",
        "области ситуации — в SITUATION_AREAS",
        "AFFECTED_SERVICES есть только если услуги собраны",
        "EVENT_DATE не подменяет дату оказания услуги или получения претензии",
        "SERVICE_TYPE, SERVICE_DATE, INCIDENT_DATE и CLAIM_DATE — поля прежней анкеты",
        "их в requiredFactKeys автоматически как шаблонные обязательные реквизиты",
        "Не подменяй отсутствующее поле похожим, не переименовывай зависимости",
        "Отсутствующие ключи enum не запрещены",
        "Нельзя убирать реальную зависимость, чтобы получить разрешение на ответ",
    ):
        assert instruction in prompt


@pytest.mark.parametrize("mode", ["GUIDED_V2", "UNSUPPORTED"])
@pytest.mark.parametrize("dependency", [FactKey.SERVICE_TYPE, FactKey.EVENT_DATE])
def test_real_missing_or_unknown_dependency_is_preserved_and_blocks_analysis(mode, dependency):
    async def scenario():
        facts = {
            "INTAKE_VERSION": mode,
            "EVENT_SUMMARY": "Синтетическое ожидание приёма.",
            "EVENT_DATE": {"date": None, "precision": "UNKNOWN"},
        }
        projection = _projection().model_copy(update={"facts": facts})
        proposal = _claim_response()
        proposal["claims"][0]["requiredFactKeys"] = [dependency.value]
        researcher = FakeHermes(name="researcher", response=proposal)
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        reasoning = await LegalReasoningOrchestrator(
            researcher=cast(HermesClient, researcher),
            reviewer=cast(HermesClient, reviewer),
        ).reason(projection)
        assert reasoning.claims[0].required_fact_keys == (dependency,)
        item = projection.evidence[0]
        evidence = ApprovedLegalFragment(
            fragment_id=item.fragment_id,
            version_id=UUID("00000000-0000-0000-0000-000000000002"),
            document_id=UUID("00000000-0000-0000-0000-000000000003"),
            article=None, part=None, point="1", structural_path=item.structural_path,
            fragment_text=item.text, text_sha256="a" * 64,
            effective_from=item.effective_from, effective_to=item.effective_to,
            source_url=item.source_url, raw_sha256="b" * 64,
            document_title=item.document_title, issuer="Synthetic authority",
            official_number=item.official_number, version_date=date(2026, 1, 1),
            publication_date=date(2026, 1, 1),
        )
        decision = verify_claims(
            reasoning.claims, evidence=[evidence],
            facts={FactKey(key): value for key, value in facts.items()},
            as_of_date=projection.as_of_date, semantic_reviews=reasoning.semantic_reviews,
        )
        assert decision.analysis_allowed is False
        assert decision.claims[0].result is VerificationResult.INSUFFICIENT_FACTS
        assert decision.claims[0].reason_code == f"{dependency.value}_UNKNOWN"

    asyncio.run(scenario())
