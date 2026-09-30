"""The v2 draft branch is durable without changing existing v1 drafts."""

import os
from uuid import uuid4

import pytest

from test_case_api import actor_headers, application_client, seed_admin

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


def _v2_data() -> dict[str, object]:
    return {
        "intakeVersion": 2,
        "incomingKind": "COMPLAINT",
        "incomingSourceStatus": "NOT_ATTACHED",
        "situationAreas": ["TREATMENT"],
        "affectedServices": ["терапевтическое лечение"],
        "eventSummary": "После лечения клиент сообщил о дискомфорте.",
        "eventDate": {"date": "2026-09-01", "precision": "EXACT"},
        "conflictStage": "ONGOING",
        "clinicActions": ["INVITED_FOR_EXAMINATION"],
        "healthSignals": ["UNKNOWN"],
        "caseMaterialsStatus": "NOT_ATTACHED",
    }


def test_v2_draft_starts_in_own_state_and_rejects_cross_version_update() -> None:
    actor = 8_100_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    with application_client() as client:
        created = client.post(
            "/v1/telegram-intake-drafts",
            json={"intakeSchemaVersion": "dental-case-intake.v2"},
            headers=actor_headers(actor, uuid4()),
        )
        assert created.status_code == 201, created.text
        draft = created.json()
        assert draft["wizardState"] == "INCOMING"
        assert draft["draftData"] == {"intakeVersion": 2}

        saved = client.put(
            f"/v1/telegram-intake-drafts/{draft['id']}",
            json={
                "expectedRevision": draft["revision"],
                "wizardState": "SUMMARY",
                "draftData": _v2_data(),
            },
            headers=actor_headers(actor, uuid4()),
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["draftData"] == _v2_data()

        mixed = client.put(
            f"/v1/telegram-intake-drafts/{draft['id']}",
            json={
                "expectedRevision": saved.json()["revision"],
                "wizardState": "INCIDENT",
                "draftData": _v2_data(),
            },
            headers=actor_headers(actor, uuid4()),
        )
        assert mixed.status_code == 422
        assert "draftData" not in mixed.text


def test_legacy_empty_create_remains_v1() -> None:
    actor = 8_200_000_000 + uuid4().int % 100_000_000
    seed_admin(actor)
    with application_client() as client:
        created = client.post(
            "/v1/telegram-intake-drafts", json={}, headers=actor_headers(actor, uuid4())
        )
    assert created.status_code == 201, created.text
    assert created.json()["wizardState"] == "INCIDENT"
    assert created.json()["draftData"] == {}
