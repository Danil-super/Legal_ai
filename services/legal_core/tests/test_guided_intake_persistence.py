"""Sparse intake uses the real tenant-scoped draft API and idempotent replay."""

import os
from uuid import uuid4

import pytest
from telegram_gateway.intake_experience import confirmed_candidates, next_missing_state

from test_case_api import actor_headers, application_client, seed_admin

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL",
)


def test_sparse_draft_survives_readback_and_replay_but_not_foreign_clinic_access():
    actor = 7_800_000_000 + uuid4().int % 100_000_000
    foreign = 7_900_000_000 + uuid4().int % 100_000_000
    inactive = 8_000_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    seed_admin(foreign)
    seed_admin(inactive, entitlement_status="INACTIVE")
    proposal = confirmed_candidates(
        "После установки коронки появился скол. Пациент требует вернуть 35 000 рублей. "
        "Письменной претензии нет."
    )
    assert next_missing_state(proposal) == "SERVICE_DATE"
    assert "problem_summary" in proposal and "demand_amount_kopecks" in proposal
    create_key, save_key = uuid4(), uuid4()
    with application_client() as client:
        created = client.post("/v1/telegram-intake-drafts", json={},
                              headers=actor_headers(actor, create_key))
        assert created.status_code in {200, 201}, created.text
        initial = created.json()
        replay = client.post("/v1/telegram-intake-drafts", json={},
                             headers=actor_headers(actor, create_key))
        assert replay.json() == initial
        path = f"/v1/telegram-intake-drafts/{initial['id']}"
        request = {"expectedRevision": initial["revision"], "wizardState": "SERVICE_DATE",
                   "draftData": proposal}
        saved = client.put(path, json=request, headers=actor_headers(actor, save_key))
        assert saved.status_code == 200, saved.text
        assert saved.json()["draftData"] == proposal
        assert saved.json()["revision"] > initial["revision"]
        replay = client.put(path, json=request, headers=actor_headers(actor, save_key))
        assert replay.status_code == 200, replay.text
        assert replay.json() == saved.json()
        resumed = client.get(path, headers=actor_headers(actor))
        assert resumed.status_code == 200
        assert resumed.json()["draftData"] == proposal
        assert client.get(path, headers=actor_headers(foreign)).status_code == 404
        assert client.put(path, json=request,
                          headers=actor_headers(foreign, uuid4())).status_code == 404
        assert client.get(path, headers=actor_headers(inactive)).status_code == 403
        # Sparse prefill is not a back door for model/risk/evidence state.
        invalid = {**request, "draftData": {**proposal, "risk_level": "LOW"}}
        assert client.put(path, json=invalid,
                          headers=actor_headers(actor, uuid4())).status_code == 422
        listing = client.get("/v1/telegram-intake-drafts", headers=actor_headers(actor))
        assert len(listing.json()["items"]) == 1
