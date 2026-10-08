import asyncio
import os
from datetime import date
from uuid import uuid4

import httpx2
import pytest

from legal_core.reference_workbook_import import build_plan, import_plan
from test_case_api import actor_headers, application_client, seed_admin
from test_reference_evaluation_api import FakeReferenceEvaluationStore, _add_member, _grant
from test_reference_workbook_import import row

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


def test_imported_drafts_replay_without_duplicates_and_lawyer_can_read_full_material():
    owner = 93_000_000_000 + uuid4().int % 1_000_000_000
    lawyer, outsider = owner + 2_000_000_000, owner + 3_000_000_000
    clinic, membership = seed_admin(owner)
    reviewer = _add_member(clinic, lawyer)
    seed_admin(outsider)
    _grant(clinic, membership, "CONTRIBUTOR")
    _grant(clinic, reviewer, "REVIEWER")
    store = FakeReferenceEvaluationStore()
    plan = build_plan("a" * 64, [row()], evaluation_date=date(2026, 10, 8))
    with application_client(reference_evaluation_store=store) as client:
        def forward(request):
            response = client.request(
                request.method, request.url.path,
                headers=dict(request.headers), content=request.content,
            )
            return httpx2.Response(response.status_code, content=response.content)

        async def run():
            async with httpx2.AsyncClient(
                base_url="http://example.test", transport=httpx2.MockTransport(forward)
            ) as api:
                assert await import_plan(plan, actor=owner, client=api) == 1
                assert await import_plan(plan, actor=owner, client=api) == 1
                with pytest.raises(httpx2.HTTPStatusError):
                    await import_plan(plan, actor=outsider, client=api)

        asyncio.run(run())
        items = client.get(
            "/v1/reference-evaluations", headers=actor_headers(lawyer)
        ).json()["items"]
        assert len(items) == 1 and items[0]["status"] == "DRAFT"
        assert items[0]["hasMaterial"] is True
        assert "Synthetic service dispute" in items[0]["displayName"]
        case_id = items[0]["id"]
        download = client.get(
            f"/v1/reference-evaluations/{case_id}/material", headers=actor_headers(lawyer)
        )
        assert download.status_code == 200
        assert download.content == plan.cases[0].full_text.encode()
        assert client.get(
            f"/v1/reference-evaluations/{case_id}", headers=actor_headers(outsider)
        ).status_code == 403
        denied = client.post(
            f"/v1/reference-evaluations/{case_id}/review",
            headers=actor_headers(lawyer, uuid4()), json={"decision": "APPROVE_FOR_EVALUATION"},
        )
        assert denied.status_code == 409  # the import never submits or approves a draft
    assert len(store.objects) == 1
