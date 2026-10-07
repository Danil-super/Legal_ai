import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.database import database_url
from test_review_material_api import _client, _prepare_material, _seed_material, _seed_user

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="disposable PostgreSQL required"
)
ROOT = "/v1/legal/editor/groups/healthcare/reference-review"


def setup_reference(monkeypatch):
    key = "reference-review-test-key-00000000001"
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", key)
    actor = 8_800_000_000 + uuid4().int % 10_000_000
    _seed_user(actor, system_role="LEGAL_EDITOR")
    material, raw = _seed_material("Synthetic blank form")
    prep = _prepare_material(material, raw, kind="REFERENCE_FORM", group_key="healthcare")
    return (
        {"X-Telegram-User-Id": str(actor), "X-Legal-Editor-Gateway-Key": key},
        material,
        raw,
        prep,
    )


def request_for(preview):
    return {
        "expectedSnapshot": preview["snapshot"],
        "preparationIds": [item["preparationId"] for item in preview["ready"]],
        "originalReviewed": True,
        "referenceOnlyUnderstood": True,
    }


def ledger_counts():
    engine = create_engine(database_url())
    try:
        with engine.connect() as connection:
            return tuple(
                connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM legal_reference_review_events), "
                        "(SELECT count(*) FROM legal_approval_events), "
                        "(SELECT count(*) FROM production_legal_fragments)"
                    )
                ).one()
            )
    finally:
        engine.dispose()


def test_reference_confirmation_is_explicit_and_never_creates_legal_approval(monkeypatch):
    headers, _, _, prep = setup_reference(monkeypatch)
    with _client() as client:
        assert (
            client.get(
                ROOT + "-preview",
                headers={
                    "X-Telegram-User-Id": headers["X-Telegram-User-Id"],
                },
            ).status_code
            == 403
        )
        response = client.get(ROOT + "-preview", headers=headers)
        group = client.get("/v1/legal/editor/groups/healthcare", headers=headers)
        assert group.status_code == 200
        assert group.json()["referenceReviewableCount"] >= 1
        before_progress = group.json()["progress"]
        assert before_progress["referenceMaterials"] >= 1
        assert before_progress["reviewedReferenceMaterials"] < before_progress["referenceMaterials"]
        assert response.status_code == 200
        preview = response.json()
        assert str(prep) in [item["preparationId"] for item in preview["ready"]]
        request = request_for(preview)
        confirm = headers | {"Idempotency-Key": str(uuid4())}
        before = ledger_counts()
        assert (
            client.post(
                ROOT + "-events",
                headers=confirm,
                json=request
                | {
                    "referenceOnlyUnderstood": False,
                },
            ).status_code
            == 422
        )
        result = client.post(ROOT + "-events", headers=confirm, json=request)
        assert result.status_code == 200
        assert result.json()["reviewedCount"] == len(request["preparationIds"])
        assert client.post(ROOT + "-events", headers=confirm, json=request).json() == result.json()
        assert (
            client.post(
                ROOT + "-events",
                headers=confirm,
                json=request
                | {
                    "expectedSnapshot": "0" * 64,
                },
            ).status_code
            == 422
        )
        after = ledger_counts()
        assert after[0] == before[0] + len(request["preparationIds"])
        assert after[1:] == before[1:]
        updated = client.get("/v1/legal/editor/groups/healthcare", headers=headers).json()
        assert updated["referenceReviewableCount"] == 0
        assert updated["progress"]["reviewedReferenceMaterials"] == (
            before_progress["reviewedReferenceMaterials"] + len(request["preparationIds"])
        )
        items = updated["items"]
        for page in range(2, (updated["totalItems"] + 9) // 10 + 1):
            items.extend(client.get(
                f"/v1/legal/editor/groups/healthcare?page={page}", headers=headers
            ).json()["items"])
        assert next(item for item in items if item["preparationId"] == str(prep))[
            "referenceReviewed"
        ] is True


def test_changed_preparation_invalidates_reference_preview(monkeypatch):
    headers, material, raw, _ = setup_reference(monkeypatch)
    with _client() as client:
        preview = client.get(ROOT + "-preview", headers=headers).json()
        _prepare_material(
            material,
            raw,
            kind="REFERENCE_FORM",
            group_key="healthcare",
            title="Corrected synthetic form",
        )
        before = ledger_counts()
        response = client.post(
            ROOT + "-events",
            headers=headers
            | {
                "Idempotency-Key": str(uuid4()),
            },
            json=request_for(preview),
        )
        assert response.status_code == 409
        assert ledger_counts() == before


def test_concurrent_reference_confirmation_has_only_one_winner(monkeypatch):
    headers, _, _, _ = setup_reference(monkeypatch)
    with _client() as client:
        request = request_for(client.get(ROOT + "-preview", headers=headers).json())
        before = ledger_counts()

        def submit():
            return client.post(
                ROOT + "-events",
                headers=headers
                | {
                    "Idempotency-Key": str(uuid4()),
                },
                json=request,
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit) for _ in range(2)]
            assert sorted(f.result() for f in futures) == [200, 409]
        assert ledger_counts()[0] == before[0] + len(request["preparationIds"])
