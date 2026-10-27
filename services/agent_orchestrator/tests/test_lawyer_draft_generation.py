"""Synthetic two-profile draft generation; never proof of legal approval or live quality."""

import asyncio
import json
from copy import deepcopy
from uuid import UUID

import httpx
import pytest

from agent_orchestrator.hermes_client import (
    HermesClient,
    HermesEndpoint,
    HermesProtocolError,
    HermesUnavailable,
    HermesUnavailableReason,
)
from agent_orchestrator.lawyer_draft_generation import LawyerDraftGenerator
from agent_orchestrator.reasoning import LegalReasoningOrchestrator, ReasoningResult
from legal_core.contracts import FactKey
from legal_core.lawyer_draft_contracts import LawyerDraftContextResponse
from legal_core.verifier import ClaimKind, SemanticVerdict
from test_main import FRAGMENT_ID, _context

OPERATION = UUID("00000000-0000-0000-0000-000000000601")
ESCALATION = UUID("00000000-0000-0000-0000-000000000602")


def context():
    actual = _context()
    actual.facts = {
        "INTAKE_VERSION": "GUIDED_V2", "EVENT_SUMMARY": "Синтетический спор о возврате оплаты.",
        "SITUATION_AREAS": ["TREATMENT"], "AFFECTED_SERVICES": ["Установка коронки"],
    }
    return LawyerDraftContextResponse.model_validate({
        "schemaVersion": "lawyer-draft-context.v1", "escalationId": ESCALATION,
        "riskLevel": "HIGH", "context": actual,
        "source": {"operationId": OPERATION, "caseId": actual.case_id,
                   "factSnapshotSha256": actual.fact_snapshot_sha256, "context": {
                       "asOfDate": actual.as_of_date,
                       "evidenceTraceSha256": actual.evidence_trace_sha256,
                       "clinicDocumentContextTraceSha256":
                           actual.clinic_document_context_trace_sha256,
                       "riskPolicyVersion": actual.risk_policy_version,
                   }},
    })


def claim(index=1, *, kind="ACTION", text="Зафиксировать обращение для проверки юристом."):
    return {"claimId": f"draft-{index}", "kind": kind, "text": text,
            "evidenceFragmentIds": [str(FRAGMENT_ID)], "requiredFactKeys": ["EVENT_SUMMARY"]}


def proposal(claims=None, **change):
    return {"claims": [claim()] if claims is None else claims,
            "internalRecommendations": [], "patientDraft": None,
            "clarificationRequests": [], **change}


def run(research=None, *, supplied=None, review_change=None, fail=None):
    research = proposal() if research is None else research
    supplied = context() if supplied is None else supplied
    calls = []

    async def respond(request):
        wire = json.loads(request.content)
        calls.append((request.url.host, wire))
        if fail == request.url.host:
            raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_TRANSPORT", request=request)
        if request.url.host == "researcher.test":
            response = deepcopy(research)
        else:
            checked = json.loads(wire["messages"][1]["content"])
            response = {"reviews": [{"claimId": item["claimId"], "verdict": "SUPPORTED",
                                     "reviewedFragmentIds": item["evidenceFragmentIds"]}
                                    for item in checked["claims"]]}
            if review_change:
                review_change(response)
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": json.dumps(response)}, "finish_reason": "stop"}]})

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            reasoner = LegalReasoningOrchestrator(
                researcher=HermesClient(HermesEndpoint("http://researcher.test", "synthetic-r",
                    model="unchanged-researcher-high", timeout_seconds=65), client=http),
                reviewer=HermesClient(HermesEndpoint("http://reviewer.test", "synthetic-v",
                    model="unchanged-reviewer-high", timeout_seconds=65), client=http),
            )
            return await LawyerDraftGenerator(reasoning=reasoner).generate(supplied)

    return lambda: asyncio.run(invoke()), calls


def test_generates_unapproved_claims_and_independent_reviews_using_the_same_two_profiles():
    invoke, calls = run()
    result = invoke()
    assert isinstance(result, ReasoningResult)
    assert result.claims[0].kind is ClaimKind.ACTION
    assert result.semantic_reviews[0].verdict is SemanticVerdict.SUPPORTED
    assert result.patient_draft is None and result.internal_recommendations == ()
    assert [host for host, _ in calls] == ["researcher.test", "reviewer.test"]
    assert [wire["model"] for _, wire in calls] == [
        "unchanged-researcher-high", "unchanged-reviewer-high",
    ]
    assert all(set(wire) == {"model", "messages", "stream"} for _, wire in calls)
    system = calls[0][1]["messages"][0]["content"]
    assert "черновик для проверки юристом" in system
    assert "варианты" in system and "условия" in system
    encoded = json.dumps(calls, ensure_ascii=False)
    assert str(OPERATION) not in encoded and str(ESCALATION) not in encoded
    assert all("analysisAllowed" not in json.loads(wire["messages"][1]["content"])
               for _, wire in calls)
    assert not hasattr(result, "report_id") and not hasattr(result, "analysis_allowed")


@pytest.mark.parametrize("fault", ["case", "hash", "empty-evidence", "risk", "forged-nested"])
def test_forged_or_stale_context_is_revalidated_before_any_model_call(fault):
    supplied = context()
    if fault == "case":
        supplied.source.case_id = UUID(int=999)
    elif fault == "hash":
        supplied.context.fact_snapshot_sha256 = "f" * 64
    elif fault == "empty-evidence":
        supplied.context.evidence.clear()
    elif fault == "risk":
        supplied.__dict__["risk_level"] = "LOW"
    else:
        supplied.context.evidence[0].__dict__["fragment_text"] = object()
    invoke, calls = run(supplied=supplied)
    with pytest.raises(ValueError) as caught:
        invoke()
    assert calls == [] and caught.value.__context__ is None and caught.value.__cause__ is None


def test_projection_redacts_existing_identifiers_and_does_not_send_binding_metadata():
    supplied = context()
    supplied.context.facts["EVENT_SUMMARY"] = "Синтетический адрес patient@example.invalid."
    invoke, calls = run(supplied=supplied)
    invoke()
    encoded = json.dumps(calls, ensure_ascii=False)
    assert "patient@example.invalid" not in encoded
    assert supplied.source.fact_snapshot_sha256 not in encoded
    assert supplied.source.context.evidence_trace_sha256 not in encoded
    assert "clinicDocumentContext" in calls[0][1]["messages"][1]["content"]


def test_unknown_dependency_is_retained_for_core_instead_of_being_fabricated_or_dropped():
    research = proposal()
    research["claims"][0]["requiredFactKeys"] = ["EVENT_SUMMARY", "SERVICE_DATE"]
    invoke, calls = run(research)
    result = invoke()
    assert FactKey.SERVICE_DATE in result.claims[0].required_fact_keys
    assert "SERVICE_DATE" not in json.loads(calls[0][1]["messages"][1]["content"])["facts"]
    assert result.semantic_reviews[0].verdict is SemanticVerdict.SUPPORTED
    assert not hasattr(result, "verified")  # Model review is not Core approval.


def test_abstention_keeps_a_typed_service_clarification_without_inventing_a_plan():
    invoke, calls = run(proposal([], clarificationRequests=[{
        "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
    }]))
    result = invoke()
    assert not result.claims and not result.semantic_reviews
    assert result.clarification_requests[0].service_slot == 0
    assert result.internal_recommendations == () and result.patient_draft is None
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["already-unknown", "foreign-slot"])
def test_invalid_clarification_scope_is_not_used_as_a_repeat_question(fault):
    supplied = context()
    if fault == "already-unknown":
        supplied.context.facts["ANALYSIS_CLARIFICATION_ANSWERS"] = {"answers": [{
            "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 0,
            "value": {"date": None, "precision": "UNKNOWN"},
        }]}
    invoke, calls = run(proposal(clarificationRequests=[{
        "questionId": "SERVICE_PROVIDED_DATE", "serviceSlot": 4 if fault == "foreign-slot" else 0,
    }]), supplied=supplied)
    with pytest.raises(HermesProtocolError):
        invoke()
    assert len(calls) == 1  # A repeated/OOB question is refused before the reviewer call.


@pytest.mark.parametrize("fault", ["foreign-citation", "too-many", "too-long", "patient-draft",
                                   "free-recommendation", "no-action", "malformed", "blank", "pii"])
def test_invalid_or_overbudget_draft_is_rejected_before_semantic_review(fault):
    research = proposal()
    if fault == "foreign-citation":
        research["claims"][0]["evidenceFragmentIds"] = [str(UUID(int=999))]
    elif fault == "too-many":
        research["claims"] = [claim(index) for index in range(9)]
    elif fault == "too-long":
        research["claims"][0]["text"] = "x" * 601
    elif fault == "patient-draft":
        research["patientDraft"] = "Текст для пациента."
    elif fault == "free-recommendation":
        research["internalRecommendations"] = ["Непроверенный свободный текст."]
    elif fault == "no-action":
        research["claims"][0]["kind"] = "LEGAL"
    elif fault == "blank":
        research["claims"][0]["text"] = " "
    elif fault == "pii":
        research["claims"][0]["text"] = "SYNTHETIC_PRIVATE_MODEL private@example.invalid"
    else:
        research["claims"][0]["text"] = {"private": "SYNTHETIC_PRIVATE_MODEL"}
    invoke, calls = run(research)
    with pytest.raises(HermesProtocolError) as caught:
        invoke()
    assert len(calls) == 1 and caught.value.__context__ is None and caught.value.__cause__ is None
    assert "SYNTHETIC_PRIVATE_MODEL" not in str(caught.value)


def test_eight_action_budget_is_accepted_without_truncating_text():
    texts = []
    for index in range(8):
        prefix = f"Синтетическое действие {index}: "
        texts.append(prefix + "x" * (600 - len(prefix)))
    invoke, calls = run(proposal([claim(index, text=text) for index, text in enumerate(texts)]))
    result = invoke()
    assert [item.text for item in result.claims] == texts and len(calls) == 2


def test_reviewer_cannot_change_the_citation_and_model_unsupported_stays_unapproved():
    def foreign(response):
        response["reviews"][0]["reviewedFragmentIds"] = [str(UUID(int=999))]
    invoke, calls = run(review_change=foreign)
    with pytest.raises(HermesProtocolError):
        invoke()
    assert len(calls) == 2

    def unsupported(response):
        response["reviews"][0]["verdict"] = "UNSUPPORTED"
    invoke, _ = run(review_change=unsupported)
    assert invoke().semantic_reviews[0].verdict is SemanticVerdict.UNSUPPORTED


@pytest.mark.parametrize("stage", ["researcher.test", "reviewer.test"])
def test_timeout_keeps_structured_failure_without_transport_or_input_context(stage):
    invoke, calls = run(fail=stage)
    with pytest.raises(HermesUnavailable) as caught:
        invoke()
    assert caught.value.reason is HermesUnavailableReason.READ_TIMEOUT
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "SYNTHETIC_PRIVATE_TRANSPORT" not in str(caught.value)
    assert len(calls) == (1 if stage == "researcher.test" else 2)
