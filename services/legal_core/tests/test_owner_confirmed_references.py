"""Explicit owner's attestation differs from independent legal review."""

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from legal_core.database import owner_database_url
from test_case_api import actor_headers, application_client, seed_admin
from test_reference_analogy_retrieval import select as select_analogies
from test_reference_evaluation_api import _add_member, _grant
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)
DECISION = "OWNER_CONFIRMED_APPROVAL"
NOTE = "LAWYER_PROVIDED_AND_CONFIRMED"
SCENARIO = "Синтетический пример: пломба выпала. Решение: проверить документы об оплате."


@pytest.fixture
def workspace(isolated_database):
    owner = 158_000_000_000 + uuid4().int % 100_000_000
    clinic, membership = seed_admin(owner, role="CLINIC_OWNER")
    lawyer = owner + 1_000_000_000
    lawyer_membership = _add_member(clinic, lawyer)
    _grant(clinic, membership, "CONTRIBUTOR")
    _grant(clinic, lawyer_membership, "CONTRIBUTOR", "REVIEWER")
    engine = create_engine(owner_database_url())
    with application_client() as client:
        created = client.post(
            "/v1/reference-evaluations",
            headers=actor_headers(owner, uuid4()),
            json={
                "groupKey": "healthcare",
                "asOfDate": "2026-10-08",
                "expectedRoute": "ABSTAIN",
                "scenarioText": SCENARIO,
            },
        )
        assert created.status_code == 201, created.text
        case_id = created.json()["case"]["id"]
        submitted = client.post(
            f"/v1/reference-evaluations/{case_id}/submit", headers=actor_headers(owner, uuid4())
        )
        assert submitted.status_code == 200, submitted.text
        detail = client.get(
            f"/v1/reference-evaluations/{case_id}", headers=actor_headers(owner)
        ).json()
        payload = {
            "decision": DECISION,
            "note": NOTE,
            "expectedReviewSnapshotSha256": detail["reviewSnapshotSha256"],
        }
        yield client, owner, lawyer, clinic, membership, lawyer_membership, case_id, payload, engine
    engine.dispose()


def test_owner_attestation_is_audited_idempotent_and_used_as_analogy(workspace):
    client, owner, _, clinic, membership, _, case_id, payload, engine = workspace
    key = uuid4()
    first = client.post(
        f"/v1/reference-evaluations/{case_id}/review",
        headers=actor_headers(owner, key),
        json=payload,
    )
    assert first.status_code == 200, first.text
    assert first.json()["decision"] == DECISION
    assert first.json()["case"]["status"] == "APPROVED_FOR_EVALUATION"
    repeated = client.post(
        f"/v1/reference-evaluations/{case_id}/review",
        headers=actor_headers(owner, key),
        json=payload,
    )
    assert repeated.status_code == 200 and repeated.json() == first.json()
    with engine.connect() as c:
        event = c.execute(
            text(
                "SELECT decision,note,reviewer_membership_id FROM "
                "reference_evaluation_review_events WHERE reference_case_id=:id"
            ),
            {"id": UUID(case_id)},
        ).one()
        assert event.decision == DECISION and event.note == NOTE
        assert event.reviewer_membership_id == membership
        assert (
            c.scalar(
                text(
                    "SELECT count(*) FROM reference_evaluation_access_grants WHERE "
                    "clinic_id=:clinic AND membership_id=:member AND permission='REVIEWER'"
                ),
                {"clinic": clinic, "member": membership},
            )
            == 0
        )
    examples = select_analogies(owner, summary="Пломба выпала").examples
    assert len(examples) == 1 and examples[0].scenario_text == SCENARIO
    assert examples[0].purpose == "NON_AUTHORITATIVE_ANALOGY"


@pytest.mark.parametrize(
    "failure", ["lawyer", "missing_note", "stale", "no_grant", "expired", "foreign", "revoked"]
)
def test_owner_confirmation_rejects_unconsented_or_unauthorized_sources(workspace, failure):
    client, owner, lawyer, _, membership, _, case_id, payload, engine = workspace
    actor = owner
    expected = 403
    if failure == "lawyer":
        actor = lawyer
    elif failure == "missing_note":
        payload.pop("note")
        expected = 422
    elif failure == "stale":
        payload["expectedReviewSnapshotSha256"] = "0" * 64
        expected = 409
    elif failure == "foreign":
        actor = owner + 2_000_000_000
        clinic, foreign_member = seed_admin(actor, role="CLINIC_OWNER")
        _grant(clinic, foreign_member, "CONTRIBUTOR")
        expected = 404
    else:
        with engine.begin() as c:
            if failure == "no_grant":
                c.execute(
                    text("DELETE FROM reference_evaluation_access_grants WHERE membership_id=:id"),
                    {"id": membership},
                )
            elif failure == "revoked":
                c.execute(
                    text("UPDATE clinic_users SET status='REVOKED' WHERE id=:id"),
                    {"id": membership},
                )
            else:
                c.execute(
                    text(
                        "UPDATE reference_evaluation_case_versions "
                        "SET content_expires_at=now()-interval '1 second' "
                        "WHERE reference_case_id=:id"
                    ),
                    {"id": UUID(case_id)},
                )
                expected = 410
    result = client.post(
        f"/v1/reference-evaluations/{case_id}/review",
        headers=actor_headers(actor, uuid4()),
        json=payload,
    )
    assert result.status_code == expected, result.text
    with engine.connect() as c:
        assert (
            c.scalar(
                text(
                    "SELECT count(*) FROM reference_evaluation_review_events "
                    "WHERE reference_case_id=:id"
                ),
                {"id": UUID(case_id)},
            )
            == 0
        )


def test_ordinary_self_review_stays_forbidden_and_db_rejects_forged_owner(workspace):
    client, owner, _, clinic, membership, lawyer_membership, case_id, payload, engine = workspace
    _grant(clinic, membership, "REVIEWER")
    ordinary = {**payload, "decision": "APPROVE_FOR_EVALUATION"}
    response = client.post(
        f"/v1/reference-evaluations/{case_id}/review",
        headers=actor_headers(owner, uuid4()),
        json=ordinary,
    )
    assert response.status_code == 403, response.text
    with engine.begin() as c:
        version = c.scalar(
            text("SELECT id FROM reference_evaluation_case_versions WHERE reference_case_id=:id"),
            {"id": UUID(case_id)},
        )
    with pytest.raises(DBAPIError), engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO reference_evaluation_review_events "
                "(clinic_id,reference_case_id,reference_version_id,"
                "reviewer_membership_id,decision,note) "
                "VALUES (:clinic,:case,:version,:reviewer,:decision,:note)"
            ),
            {
                "clinic": clinic,
                "case": UUID(case_id),
                "version": version,
                "reviewer": lawyer_membership,
                "decision": DECISION,
                "note": NOTE,
            },
        )


def test_owner_approval_cannot_be_silently_erased_by_migration_downgrade(workspace):
    from alembic import command
    from alembic.config import Config
    from test_verified_copy_analysis_e2e import ROOT

    client, owner, _, _, _, _, case_id, payload, engine = workspace
    result = client.post(
        f"/v1/reference-evaluations/{case_id}/review",
        headers=actor_headers(owner, uuid4()),
        json=payload,
    )
    assert result.status_code == 200, result.text
    with pytest.raises(RuntimeError, match="retained owner reference attestations"):
        command.downgrade(Config(str(ROOT / "alembic.ini")), "a53e0f7b1c42")
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "b64f1a8c2d53"
        assert (
            c.scalar(
                text(
                    "SELECT count(*) FROM reference_evaluation_review_events "
                    "WHERE reference_case_id=:id"
                ),
                {"id": UUID(case_id)},
            )
            == 1
        )


def test_empty_attestation_migration_roundtrip_preserves_independent_review_guard(
    isolated_database,
):
    from alembic import command
    from alembic.config import Config
    from test_verified_copy_analysis_e2e import ROOT

    config = Config(str(ROOT / "alembic.ini"))
    command.downgrade(config, "a53e0f7b1c42")
    command.upgrade(config, "head")


@pytest.mark.parametrize("withdraw", ["grant", "subscription-expiry", "source-expiry"])
def test_owner_approval_rechecks_authority_and_expiry_after_fk_wait(workspace, withdraw):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from test_reference_review_source_snapshot import _lock_reviewer, _observed_insert_fk_wait

    client, owner, _, _, membership, _, case_id, payload, engine = workspace
    if withdraw in {"subscription-expiry", "source-expiry"}:
        with engine.begin() as c:
            if withdraw == "subscription-expiry":
                c.execute(
                    text(
                        "UPDATE subscription_entitlements SET ends_at="
                        "clock_timestamp()+interval '2 seconds' "
                        "WHERE user_id=(SELECT user_id FROM clinic_users WHERE id=:id)"
                    ),
                    {"id": membership},
                )
            else:
                c.execute(
                    text(
                        "UPDATE reference_evaluation_case_versions SET content_expires_at="
                        "clock_timestamp()+interval '2 seconds' WHERE reference_case_id=:id"
                    ),
                    {"id": UUID(case_id)},
                )
    key = uuid4()
    with engine.connect() as holder, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = holder.begin()
        _lock_reviewer(holder, membership)
        future = pool.submit(
            client.post,
            f"/v1/reference-evaluations/{case_id}/review",
            headers=actor_headers(owner, key),
            json=payload,
        )
        blocked = _observed_insert_fk_wait(engine)
        if withdraw == "grant":
            holder.execute(
                text("DELETE FROM reference_evaluation_access_grants WHERE membership_id=:id"),
                {"id": membership},
            )
        else:
            time.sleep(2.1)
        transaction.commit()
        response = future.result(timeout=5)
    assert blocked, response.text
    assert response.status_code == (410 if withdraw == "source-expiry" else 403), response.text
    with engine.connect() as c:
        assert (
            c.scalar(
                text(
                    "SELECT count(*) FROM reference_evaluation_review_events "
                    "WHERE reference_case_id=:id"
                ),
                {"id": UUID(case_id)},
            )
            == 0
        )
        assert (
            c.scalar(
                text("SELECT count(*) FROM idempotency_records WHERE key=:key"), {"key": str(key)}
            )
            == 0
        )
        assert (
            c.scalar(
                text("SELECT status FROM reference_evaluation_cases WHERE id=:id"),
                {"id": UUID(case_id)},
            )
            == "READY_FOR_REVIEW"
        )
