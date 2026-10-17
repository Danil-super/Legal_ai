"""Actor-facing operations are fast, durable and never reveal internal dispatch claims."""

import asyncio
import os

import httpx
import pytest

from legal_core.intake_preparation_runner import IntakePreparationRunner
from legal_core.main import create_app
from legal_core.model_admission import ModelAdmission
from test_case_api import actor_headers, application_client
from test_intake_preparation_lifecycle import _run, draft_owner
from test_intake_preparation_runner import SETTINGS, _wire

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]


def test_actor_start_read_cancel_restart_keep_one_unconfirmed_operation(draft_owner):
    actor, _, _, draft = draft_owner

    async def scenario(sessions):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request)
            entered.set()
            await release.wait()
            return httpx.Response(200, json=_wire(request))

        app = create_app(session_factory=sessions, managed_engine=sessions.kw["bind"],
                         enable_analysis_worker=False, enable_draft_retention=False)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as agent:
            runner = IntakePreparationRunner(sessions, SETTINGS, ModelAdmission(), client=agent)
            app.state.intake_preparation_runner = runner
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://core:8000") as client:
                    path = f"/v1/telegram-intake-drafts/{draft['id']}/understanding"
                    kwargs = {"headers": actor_headers(actor),
                              "json": {"expectedRevision": draft["revision"]}}
                    first = await asyncio.wait_for(client.post(path, **kwargs), timeout=2)
                    assert first.status_code == 202, first.text
                    result = first.json()
                    assert set(result) == {"operationId", "state", "suggestions",
                                           "source", "acceptance"}
                    assert result["state"] == "PENDING" and result["suggestions"] is None
                    await asyncio.wait_for(entered.wait(), timeout=2)
                    replay = await client.post(path, **kwargs)
                    assert replay.json() == result and len(calls) == 1
                    release.set()
                    await asyncio.gather(*runner._tasks)
                    operation_path = f"/v1/intake-preparations/{result['operationId']}"
                    completed = await client.get(operation_path, headers=actor_headers(actor))
                    assert completed.status_code == 200
                    assert completed.json()["state"] == "PROPOSED"
                    assert completed.json()["suggestions"]["clinicActions"] == [
                        "INVITED_FOR_EXAMINATION",
                    ]
                    assert "leaseToken" not in completed.text and "intent" not in completed.json()
                    cancelled = await client.delete(operation_path, headers=actor_headers(actor))
                    assert cancelled.status_code == 200
                    assert cancelled.json()["state"] == "MANUAL_CONTINUATION"
                    assert cancelled.json()["suggestions"] is None
                    replay = await client.post(path, **kwargs)
                    assert replay.json() == cancelled.json() and len(calls) == 1
                    actual = await client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                                              headers=actor_headers(actor))
                    assert actual.json()["draftData"] == draft["draftData"]
                    assert actual.json()["revision"] == draft["revision"]
            finally:
                release.set()
                await runner.close()
    _run(scenario)


def test_absent_runner_returns_durable_manual_continuation_instead_of_orphan_pending(draft_owner):
    actor, _, _, draft = draft_owner
    with application_client() as client:
        path = f"/v1/telegram-intake-drafts/{draft['id']}/understanding"
        response = client.post(path, headers=actor_headers(actor),
                               json={"expectedRevision": draft["revision"]})
        assert response.status_code == 202, response.text
        assert response.json()["state"] == "MANUAL_CONTINUATION"
        operation = response.json()["operationId"]
    with application_client() as restarted:
        resumed = restarted.get(f"/v1/intake-preparations/{operation}",
                                headers=actor_headers(actor))
        assert resumed.json() == response.json()


@pytest.mark.parametrize("body", [
    {"expectedRevision": True}, {"expectedRevision": "2"}, {"expectedRevision": 999},
    {"expectedRevision": 2, "source": "Arbitrary source is not accepted"},
    {"expectedRevision": 2, "clinicId": "Arbitrary tenant is not accepted"},
])
def test_actor_start_refuses_unbound_or_non_metadata_inputs(draft_owner, body):
    actor, _, _, draft = draft_owner
    with application_client() as client:
        response = client.post(f"/v1/telegram-intake-drafts/{draft['id']}/understanding",
                               headers=actor_headers(actor), json=body)
    assert response.status_code in {409, 422}, response.text


def test_actor_cannot_start_understanding_during_safe_stop(draft_owner, monkeypatch):
    actor, _, _, draft = draft_owner
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    with application_client() as client:
        response = client.post(f"/v1/telegram-intake-drafts/{draft['id']}/understanding",
                               headers=actor_headers(actor),
                               json={"expectedRevision": draft["revision"]})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ANALYSIS_SAFE_STOP"


def test_discovery_does_not_dispatch_and_acceptance_replays_after_restart(draft_owner, monkeypatch):
    from test_intake_preparation_acceptance import _proposal

    actor, _, _, draft = draft_owner
    path = f"/v1/telegram-intake-drafts/{draft['id']}/understanding"
    with application_client() as client:
        absent = client.get(path, headers=actor_headers(actor))
        assert absent.status_code == 200, absent.text
        assert absent.json() == {"operation": None}
    result, request = _proposal(actor, draft)
    operation_path = f"/v1/intake-preparations/{result.operation_id}"
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    with application_client() as client:
        discovered = client.get(path, headers=actor_headers(actor))
        assert discovered.status_code == 200, discovered.text
        status = discovered.json()["operation"]
        assert status["operationId"] == str(result.operation_id)
        assert status["source"] == result.source.model_dump(mode="json", by_alias=True)
        accepted = client.post(operation_path + "/acceptance", headers=actor_headers(actor),
                               json=request.model_dump(mode="json", by_alias=True))
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["acceptedRevision"] == draft["revision"] + 1
    with application_client() as restarted:
        replay = restarted.post(operation_path + "/acceptance", headers=actor_headers(actor),
                                json=request.model_dump(mode="json", by_alias=True))
        assert replay.json() == accepted.json()
        status = restarted.get(operation_path, headers=actor_headers(actor)).json()
        assert status["state"] == "ACCEPTED"
        assert status["source"] is None and status["suggestions"] is None
        assert status["acceptance"] == {"draftId": draft["id"],
                                        "acceptedRevision": draft["revision"] + 1}
        assert restarted.get(path, headers=actor_headers(actor)).json() == {"operation": None}
        assert "leaseToken" not in replay.text and "intent" not in replay.json()


@pytest.mark.parametrize("patch", [
    {"expectedRevision": True}, {"expectedRevision": "2"}, {"expectedRevision": 0},
    {"expectedProposalSha256": "not-a-hash"}, {"suggestions": {}}, {"clinicId": "foreign"},
    {"confirmed": True}, {"leaseToken": "private"},
])
def test_acceptance_refuses_client_fact_payload_or_non_strict_selectors(draft_owner, patch):
    from test_intake_preparation_acceptance import _proposal

    actor, _, _, draft = draft_owner
    result, request = _proposal(actor, draft)
    with application_client() as client:
        response = client.post(f"/v1/intake-preparations/{result.operation_id}/acceptance",
                               headers=actor_headers(actor), json={
                                   **request.model_dump(mode="json", by_alias=True), **patch,
                               })
        assert response.status_code == 422, response.text
        assert client.get(f"/v1/telegram-intake-drafts/{draft['id']}",
                          headers=actor_headers(actor)).json()["revision"] == draft["revision"]
