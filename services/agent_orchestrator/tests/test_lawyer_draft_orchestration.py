"""Actual Agent HTTP and Core/Hermes transports; no live models or permissions."""

import json
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint
from agent_orchestrator.legal_core_client import LegalCoreClient, LegalCoreEndpoint
from agent_orchestrator.main import ServiceDependencies, ServiceSettings, create_app
from agent_orchestrator.reasoning import LegalReasoningOrchestrator
from legal_core.case_preparation_contracts import InternalWorkProduct
from legal_core.lawyer_work_product_contracts import LawyerWorkProductView
from test_lawyer_draft_generation import context, proposal
from test_main import CASE_ID, IDEMPOTENCY_KEY, INTERNAL_KEY

TOKEN = UUID(int=8001)


def retained_view(source):
    content = InternalWorkProduct(
        schemaVersion="ai-first-work-product.v1", kind="LAWYER_REVIEW_DRAFT",
        workProductId=UUID(int=8002), version=1, parentVersion=None, clinicId=UUID(int=8003),
        source=source, asOfDate=source.context.as_of_date,
        riskPolicyVersion=source.context.risk_policy_version, status="UNAPPROVED",
        summary="Синтетический спор.", actions=["Зафиксировать обращение."], uncertainties=[],
        legalFragmentIds=[context().context.evidence[0].fragment_id],
    )
    return LawyerWorkProductView(
        versionId=UUID(int=8004), reviewState="UNAPPROVED", origin="AI", isCurrent=True,
        contentSha256=content.content_sha256, content=content, citations=context().context.evidence,
    ).model_dump(mode="json", by_alias=True)


@pytest.fixture
def scenario(monkeypatch):
    monkeypatch.setenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "true")
    supplied = context()
    original = {
        "resultVersion": 2, "status": "HUMAN_REVIEW",
        "source": {**supplied.source.model_dump(mode="json", by_alias=True), "context": None},
        "escalationId": str(supplied.escalation_id), "riskLevel": supplied.risk_level,
        "riskPolicyVersion": supplied.context.risk_policy_version,
    }
    state = {"fault": None, "retained": None, "model": proposal(), "slow": False,
             "initial_context": False, "verdict": "SUPPORTED"}
    requests = []

    async def respond(request):
        import asyncio

        path = request.url.path
        wire = json.loads(request.content) if request.content else None
        requests.append((request.url.host, path, wire, dict(request.headers)))
        if request.url.host == "core.test":
            if path.endswith("preparation-context"):
                if state["initial_context"]:
                    return httpx.Response(200, json=supplied.context.model_dump(
                        mode="json", by_alias=True,
                    ))
                return httpx.Response(201, json=original)
            if path.endswith("preparation-submissions"):
                original["source"]["context"] = supplied.source.context.model_dump(
                    mode="json", by_alias=True,
                )
                return httpx.Response(201, json=original)
            assert request.headers["Idempotency-Key"] == str(IDEMPOTENCY_KEY)
            assert request.headers["X-Analysis-Job-Id"] == str(IDEMPOTENCY_KEY)
            assert request.headers["X-Analysis-Job-Token"] == str(TOKEN)
            assert request.headers["X-Agent-Internal-Key"] == INTERNAL_KEY
            assert request.headers["X-Telegram-User-Id"] == "123"
            if state["fault"] == path.rsplit("/", 1)[-1]:
                return httpx.Response(409, json={"error": {
                    "code": "SYNTHETIC_PRIVATE_CODE", "message": "SYNTHETIC_PRIVATE_DETAIL",
                }})
            if path.endswith("lookup"):
                return httpx.Response(200, content=json.dumps(state["retained"]),
                                      headers={"content-type": "application/json"})
            if path.endswith("lawyer-draft-context"):
                assert wire == {
                    "expectedPreparationOperationId": original["source"]["operationId"],
                    "expectedFactSnapshotSha256": original["source"]["factSnapshotSha256"],
                    "expectedRiskPolicyVersion": original["riskPolicyVersion"],
                }
                return httpx.Response(200, json=supplied.model_dump(mode="json", by_alias=True))
            assert path.endswith("lawyer-work-products")
            assert set(wire) == {"expectedSource", "claims", "semanticReviews"}
            assert wire["expectedSource"] == supplied.source.model_dump(mode="json", by_alias=True)
            state["retained"] = retained_view(supplied.source)
            if state["fault"] == "lost-submit-response":
                raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_DETAIL", request=request)
            return httpx.Response(201, json=state["retained"])
        if state["slow"]:
            await asyncio.Event().wait()
        if state["fault"] == request.url.host:
            raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_DETAIL", request=request)
        if request.url.host == "researcher.test":
            return httpx.Response(200, json={"choices": [{"message": {
                "content": json.dumps(state["model"])}, "finish_reason": "stop"}]})
        checked = json.loads(wire["messages"][1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "reviews": [{"claimId": claim["claimId"], "verdict": state["verdict"],
                         "reviewedFragmentIds": claim["evidenceFragmentIds"]}
                        for claim in checked["claims"]],
        })}, "finish_reason": "stop"}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    core = LegalCoreClient(LegalCoreEndpoint("http://core.test", INTERNAL_KEY), client=http)
    reasoner = LegalReasoningOrchestrator(
        researcher=HermesClient(HermesEndpoint("http://researcher.test", "synthetic-r"),
                                client=http),
        reviewer=HermesClient(HermesEndpoint("http://reviewer.test", "synthetic-v"), client=http),
    )
    settings = ServiceSettings(INTERNAL_KEY, "http://core.test", "http://researcher.test", "r",
                               "existing-high-r", "http://reviewer.test", "v", "existing-high-v")
    app = create_app(settings=settings, dependencies=ServiceDependencies(core, reasoner))
    with TestClient(app) as c:
        yield c, state, requests, original, supplied


def analyze(scenario, *, headers=None):
    return scenario[0].post(f"/v1/cases/{CASE_ID}/analyze", headers={
        "X-Agent-Internal-Key": INTERNAL_KEY, "X-Telegram-User-Id": "123",
        "Idempotency-Key": str(IDEMPOTENCY_KEY), "X-Analysis-Result-Version": "2",
        "X-Analysis-Job-Id": str(IDEMPOTENCY_KEY), "X-Analysis-Job-Token": str(TOKEN),
        **(headers or {}),
    })


def test_human_review_gets_a_draft_and_replay_skips_models(scenario):
    response = analyze(scenario)
    assert response.status_code == 200 and response.json() == scenario[3]
    assert scenario[1]["retained"] is not None
    assert [host for host, *_ in scenario[2]] == [
        "core.test", "core.test", "core.test", "researcher.test", "reviewer.test", "core.test",
    ]
    assert analyze(scenario).json() == scenario[3]
    assert sum(host == "researcher.test" for host, *_ in scenario[2]) == 1
    assert scenario[2][-1][1].endswith("lookup")


@pytest.mark.parametrize("fault", ["lookup", "lawyer-draft-context", "lawyer-work-products",
                                   "researcher.test", "reviewer.test", "lost-submit-response"])
def test_optional_draft_failures_preserve_original_human_review(scenario, fault, caplog):
    scenario[1]["fault"] = fault
    response = analyze(scenario)
    assert response.status_code == 200 and response.json() == scenario[3]
    assert "SYNTHETIC_PRIVATE" not in response.text + caplog.text
    expected = ("lawyer-work-products" if fault == "lost-submit-response" else fault)
    assert any(host == expected or path.endswith(expected) for host, path, *_ in scenario[2])
    if fault == "lost-submit-response":
        before = sum(host == "researcher.test" for host, *_ in scenario[2])
        scenario[1]["fault"] = None
        assert analyze(scenario).json() == scenario[3]
        assert sum(host == "researcher.test" for host, *_ in scenario[2]) == before


def test_abstention_never_submits_a_fake_plan(scenario):
    scenario[1]["model"] = proposal([])
    assert analyze(scenario).json() == scenario[3]
    assert scenario[1]["retained"] is None
    assert not any(host == "reviewer.test" for host, *_ in scenario[2])


@pytest.mark.parametrize("flag", [None, "false", "0", "off", "invalid"])
def test_feature_is_explicitly_off_by_default(scenario, monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("AI_FIRST_LAWYER_DRAFT_ENABLED")
    else:
        monkeypatch.setenv("AI_FIRST_LAWYER_DRAFT_ENABLED", flag)
    assert analyze(scenario).json() == scenario[3]
    assert len(scenario[2]) == 1


def test_numeric_deploy_flag_uses_the_same_enabled_path(scenario, monkeypatch):
    monkeypatch.setenv("AI_FIRST_LAWYER_DRAFT_ENABLED", "1")
    assert analyze(scenario).json() == scenario[3]
    assert scenario[1]["retained"] is not None


def test_optional_wall_timeout_returns_receipt_before_parent_deadline(scenario, monkeypatch):
    monkeypatch.setattr(
        "agent_orchestrator.lawyer_draft_orchestration.DRAFT_WALL_TIMEOUT_SECONDS", 0.01,
    )
    scenario[1]["slow"] = True
    response = analyze(scenario)
    assert response.status_code == 200 and response.json() == scenario[3]
    assert scenario[1]["retained"] is None


def test_late_human_review_uses_its_actual_retained_source_too(scenario):
    scenario[1]["initial_context"] = True
    response = analyze(scenario)
    assert response.status_code == 200 and response.json() == scenario[3]
    assert scenario[1]["retained"] is not None
    assert scenario[3]["source"]["context"] is not None
    assert sum(host == "researcher.test" for host, *_ in scenario[2]) == 2


def test_critical_routing_is_preserved_while_the_same_draft_flow_runs(scenario):
    scenario[3]["riskLevel"] = "CRITICAL"
    scenario[4].risk_level = "CRITICAL"
    assert analyze(scenario).json() == scenario[3]
    assert scenario[1]["retained"] is not None


def test_unverified_reviews_are_submitted_unchanged_for_core_to_refuse(scenario):
    scenario[1]["verdict"] = "UNSUPPORTED"
    scenario[1]["fault"] = "lawyer-work-products"
    response = analyze(scenario)
    assert response.status_code == 200 and response.json() == scenario[3]
    sent = scenario[2][-1][2]
    assert sent["semanticReviews"][0]["verdict"] == "UNSUPPORTED"
    assert sent["claims"] and scenario[1]["retained"] is None


def test_missing_job_lease_never_starts_optional_models(scenario):
    headers = {"X-Agent-Internal-Key": INTERNAL_KEY, "X-Telegram-User-Id": "123",
               "Idempotency-Key": str(IDEMPOTENCY_KEY), "X-Analysis-Result-Version": "2"}
    response = scenario[0].post(f"/v1/cases/{CASE_ID}/analyze", headers=headers)
    assert response.status_code == 200 and response.json() == scenario[3]
    assert len(scenario[2]) == 1


@pytest.mark.parametrize("fault", ["escalation", "risk"])
def test_private_context_cannot_rebind_the_routed_escalation(scenario, fault):
    if fault == "escalation":
        scenario[4].escalation_id = UUID(int=9111)
    else:
        scenario[4].risk_level = "CRITICAL"
    response = analyze(scenario)
    assert response.status_code == 200 and response.json() == scenario[3]
    assert all(host == "core.test" for host, *_ in scenario[2])


def test_short_parent_budget_preserves_the_durable_receipt(scenario, monkeypatch):
    monkeypatch.setattr("agent_orchestrator.main.ANALYSIS_WALL_TIMEOUT_SECONDS", 0.5)
    assert analyze(scenario).json() == scenario[3]
    assert len(scenario[2]) == 1


def test_overall_deadline_after_routing_still_returns_the_committed_receipt(scenario, monkeypatch):
    import asyncio

    async def delayed_optional(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr("agent_orchestrator.main.maybe_generate_lawyer_draft", delayed_optional)
    monkeypatch.setattr("agent_orchestrator.main.ANALYSIS_WALL_TIMEOUT_SECONDS", 0.02)
    response = analyze(scenario)
    assert response.status_code == 200 and response.json() == scenario[3]
