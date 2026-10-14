"""Read-only audit regression proposals; synthetic fixtures in an isolated local DB."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.database import owner_database_url

from test_case_api import actor_headers, application_client, seed_admin, workflow_submission
from test_reference_evaluation_api import (
    FakeReferenceEvaluationStore,
    _add_member,
    _create_payload,
    _grant,
)

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires isolated disposable PostgreSQL"
)


def test_concurrent_report_creation_serializes_versions(monkeypatch):
    actor = 115_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    original = AsyncSession.scalar
    gate = asyncio.Event()
    calls = 0

    async def concurrent_max(self, statement, *args, **kwargs):
        nonlocal calls
        value = await original(self, statement, *args, **kwargs)
        if "coalesce(max(case_reports.report_version)" in str(statement):
            calls += 1
            if calls == 2:
                gate.set()
            # Force the vulnerable interleaving when both reads can occur. A fixed
            # case lock may serialize them; then release the first after this window.
            with suppress(TimeoutError):
                await asyncio.wait_for(gate.wait(), timeout=0.5)
        return value

    with application_client() as client:
        workflow = client.post(
            f"/v1/telegram-case-workflows/{uuid4()}/submissions",
            headers=actor_headers(actor),
            json=workflow_submission(),
        )
        assert workflow.status_code == 201, workflow.text
        case = workflow.json()["case"]["id"]
        monkeypatch.setattr(AsyncSession, "scalar", concurrent_max)

        def create():
            try:
                response = client.post(
                    f"/v1/cases/{case}/reports",
                    headers=actor_headers(actor, uuid4()),
                    json={"locale": "ru-RU"},
                )
                return response.status_code, response.text
            except Exception as exc:
                return type(exc).__name__, str(exc).split("\n", 2)[:2]

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: create(), range(2)))
    assert [status for status, _ in outcomes] == [201, 201], outcomes


def test_a_reviewer_cannot_supply_material_then_approve_the_same_package():
    actor = 116_000_000_000 + uuid4().int % 100_000_000
    reviewer = actor + 1_000_000_000
    clinic, member = seed_admin(actor, role="CLINIC_OWNER")
    reviewer_member = _add_member(clinic, reviewer)
    _grant(clinic, member, "CONTRIBUTOR")
    _grant(clinic, reviewer_member, "CONTRIBUTOR", "REVIEWER")
    with application_client(reference_evaluation_store=FakeReferenceEvaluationStore()) as client:
        created = client.post(
            "/v1/reference-evaluations",
            headers=actor_headers(actor, uuid4()),
            json=_create_payload(),
        )
        assert created.status_code == 201, created.text
        case = created.json()["case"]["id"]
        supplied = client.post(
            f"/v1/reference-evaluations/{case}/material",
            headers={
                **actor_headers(reviewer, uuid4()),
                "X-Source-Filename": "synthetic.txt",
                "Content-Type": "text/plain",
            },
            content=b"Synthetic independently unreviewed reference material supplied by reviewer.",
        )
        submitted = client.post(
            f"/v1/reference-evaluations/{case}/submit",
            headers=actor_headers(reviewer, uuid4()),
        )
        reviewed = client.post(
            f"/v1/reference-evaluations/{case}/review",
            headers=actor_headers(reviewer, uuid4()),
            json={"decision": "APPROVE_FOR_EVALUATION"},
        )
    assert supplied.status_code in (403, 404) or reviewed.status_code == 403, (
        supplied.status_code,
        submitted.status_code,
        reviewed.status_code,
        reviewed.text,
    )


def test_expired_reference_material_is_unavailable_before_storage_cleanup():
    actor = 118_000_000_000 + uuid4().int % 100_000_000
    clinic, member = seed_admin(actor, role="CLINIC_OWNER")
    _grant(clinic, member, "CONTRIBUTOR")
    store = FakeReferenceEvaluationStore()
    create_key, upload_key = uuid4(), uuid4()
    with application_client(reference_evaluation_store=store) as client:
        created = client.post(
            "/v1/reference-evaluations",
            headers=actor_headers(actor, create_key),
            json=_create_payload(),
        )
        assert created.status_code == 201, created.text
        case = created.json()["case"]["id"]
        uploaded = client.post(
            f"/v1/reference-evaluations/{case}/material",
            headers={
                **actor_headers(actor, upload_key),
                "X-Source-Filename": "synthetic.txt",
                "Content-Type": "text/plain",
            },
            content=b"Synthetic reference source whose retention period has expired.",
        )
        assert uploaded.status_code == 201, uploaded.text
        owner = create_engine(owner_database_url())
        try:
            with owner.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE reference_evaluation_case_versions "
                        "SET content_expires_at=now()-interval '1 minute' "
                        "WHERE reference_case_id=:case"
                    ),
                    {"case": case},
                )
        finally:
            owner.dispose()
        response = client.get(
            f"/v1/reference-evaluations/{case}/material",
            headers=actor_headers(actor),
        )
        detail = client.get(f"/v1/reference-evaluations/{case}", headers=actor_headers(actor))
        replay = client.post(
            "/v1/reference-evaluations",
            headers=actor_headers(actor, create_key),
            json=_create_payload(),
        )
        submit = client.post(
            f"/v1/reference-evaluations/{case}/submit",
            headers=actor_headers(actor, uuid4()),
        )
        upload_replay = client.post(
            f"/v1/reference-evaluations/{case}/material",
            headers={
                **actor_headers(actor, upload_key),
                "X-Source-Filename": "synthetic.txt",
                "Content-Type": "text/plain",
            },
            content=b"Synthetic reference source whose retention period has expired.",
        )
        # Leave this synthetic version outside the global retention worker's due queue.
        with owner.begin() as connection:
            connection.execute(
                text(
                    "UPDATE reference_evaluation_case_versions "
                    "SET content_expires_at=now()+interval '30 days' "
                    "WHERE reference_case_id=:case"
                ),
                {"case": case},
            )
        owner.dispose()
    assert response.status_code == 410, (response.status_code, response.text)
    assert detail.status_code == 200 and detail.json()["scenarioText"] is None
    assert detail.json()["hasMaterial"] is False
    assert replay.status_code == 410, replay.text
    assert submit.status_code == 410, submit.text
    assert upload_replay.status_code == 410, upload_replay.text
