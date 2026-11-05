"""Document-aware actions still pass through the real Legal Core verifier.

Only model I/O is synthetic. These tests establish the context/publication boundary,
not the legal quality of a live model's conclusions.
"""

import asyncio
import json
from datetime import date
from typing import cast

import pytest

from agent_orchestrator.hermes_client import HermesClient
from agent_orchestrator.reasoning import LegalReasoningOrchestrator
from legal_core.analysis_api import _verified_action_items
from legal_core.verifier import verify_claims
from test_reasoning import (
    FakeHermes, _claim_response, _projection, _projection_with_clinic_context, _review_response,
)
from test_verifier import _evidence


@pytest.mark.parametrize("clause,action", [
    (
        "Гарантийный осмотр организуется администратором после обращения.",
        "Если эта редакция применима к пациенту, организуйте осмотр по порядку положения; "
        "сначала сверьте подписанную редакцию.",
    ),
    (
        "Положение требует проверки соблюдения графика контрольных осмотров.",
        "Если эта редакция применима к пациенту, сверьте подписанную редакцию и график "
        "контрольных осмотров; само положение не доказывает нарушение пациентом графика.",
    ),
])
@pytest.mark.parametrize("verdict,published", [("SUPPORTED", True), ("UNSUPPORTED", False)])
def test_conditional_document_action_is_published_only_after_independent_review(
    clause, action, verdict, published,
):
    async def run():
        projection = _projection_with_clinic_context()
        projection.clinic_document_context[0].text = clause
        proposed = _claim_response()
        proposed["claims"][0].update(kind="ACTION", text=action)
        reviewed = _review_response()
        reviewed["reviews"][0]["verdict"] = verdict
        researcher = FakeHermes(name="researcher", response=proposed)
        reviewer = FakeHermes(name="reviewer", response=reviewed)
        result = await LegalReasoningOrchestrator(
            researcher=cast(HermesClient, researcher), reviewer=cast(HermesClient, reviewer),
        ).reason(projection)
        reviewed_context = json.loads(reviewer.users[0])
        assert reviewed_context["clinicDocumentContext"][0]["text"] == clause
        assert reviewed_context["facts"] == _projection().facts == projection.facts
        assert len(reviewed_context["evidence"]) == 1
        decision = verify_claims(
            result.claims, evidence=[_evidence(effective_from=date(2026, 1, 1))],
            facts={key: value for key, value in projection.facts.items()},
            as_of_date=projection.as_of_date, semantic_reviews=result.semantic_reviews,
        )
        actions = _verified_action_items(
            result.claims, {claim.claim_id: claim.result for claim in decision.claims},
        )
        assert actions == ([action] if published else [])
        assert decision.analysis_allowed is published

    asyncio.run(run())


def test_no_documents_keeps_document_payloads_out_of_the_review_channel():
    async def run():
        researcher = FakeHermes(name="researcher", response=_claim_response())
        reviewer = FakeHermes(name="reviewer", response=_review_response())
        await LegalReasoningOrchestrator(
            researcher=cast(HermesClient, researcher), reviewer=cast(HermesClient, reviewer),
        ).reason(_projection())
        wire = json.loads(reviewer.users[0])
        assert "clinicDocumentContext" not in wire and "caseMaterialContext" not in wire

    asyncio.run(run())
