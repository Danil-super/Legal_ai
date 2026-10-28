"""Actual runtime-role SQL selection of synthetic privately granted analogies."""

import asyncio
import hashlib
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.case_api import resolve_actor
from legal_core.contracts import FactKey
from legal_core.database import database_url, owner_database_url
from legal_core.reference_analogy_retrieval import select_reference_analogies
from test_case_api import actor_headers, application_client, seed_admin
from test_reference_evaluation_api import _add_member, _grant
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
SCENARIO = "Синтетический пример: пломба выпала, пациент просит проверить оплату."


@pytest.fixture
def workspace(isolated_database):
    actor = 141_000_000_000 + uuid4().int % 100_000_000
    clinic, membership = seed_admin(actor, role="CLINIC_OWNER")
    reviewer = actor + 1_000_000_000
    reviewer_membership = _add_member(clinic, reviewer)
    _grant(clinic, membership, "CONTRIBUTOR")
    _grant(clinic, reviewer_membership, "REVIEWER")
    owner = create_engine(owner_database_url())
    with application_client() as client:
        def create(scenario=SCENARIO, *, approve=True):
            result = client.post("/v1/reference-evaluations", headers=actor_headers(actor, uuid4()),
                                 json={"groupKey": "healthcare", "asOfDate": "2026-10-08",
                                       "expectedRoute": "ABSTAIN", "scenarioText": scenario})
            assert result.status_code == 201, result.text
            identifier = UUID(result.json()["case"]["id"])
            if approve:
                assert client.post(f"/v1/reference-evaluations/{identifier}/submit",
                    headers=actor_headers(actor, uuid4())).status_code == 200
                detail = client.get(f"/v1/reference-evaluations/{identifier}",
                                    headers=actor_headers(reviewer))
                assert detail.status_code == 200, detail.text
                response = client.post(f"/v1/reference-evaluations/{identifier}/review",
                    headers=actor_headers(reviewer, uuid4()),
                    json={"decision": "APPROVE_FOR_EVALUATION",
                          "expectedReviewSnapshotSha256": detail.json()["reviewSnapshotSha256"]})
                assert response.status_code == 200, response.text
            return identifier
        yield actor, clinic, membership, reviewer_membership, create, owner
    owner.dispose()


def select(actor, *, summary="Пломба выпала", mutate=None):
    async def invoke():
        engine = create_async_engine(database_url())
        try:
            async with async_sessionmaker(engine)() as session:
                current = await resolve_actor(session, actor)
                if mutate:
                    mutate()
                return await select_reference_analogies(
                    session, actor=current, facts={FactKey.EVENT_SUMMARY: summary})
        finally:
            await engine.dispose()
    return asyncio.run(invoke())


def test_granted_creator_gets_only_complete_reviewed_scenario_and_bound_provenance(workspace):
    actor, _, _, _, create, _ = workspace
    case = create()
    result = select(actor)
    assert len(result.examples) == 1
    item = result.examples[0]
    assert item.scenario_text == SCENARIO and item.source.reference_case_id == case
    assert item.source.scenario_sha256 == hashlib.sha256(SCENARIO.encode()).hexdigest()
    assert item.source.evaluation_as_of_date.isoformat() == "2026-10-08"
    assert item.purpose == "NON_AUTHORITATIVE_ANALOGY"
    wire = result.model_dump_json(by_alias=True)
    assert all(key not in wire for key in ("rawObjectKey", "reviewerMembershipId", "expectedRoute"))


@pytest.mark.parametrize("fault", ["ungranted", "same-clinic-ungranted", "revoked-grant",
                                   "revoked-member", "revoked-user", "entitlement", "foreign"])
def test_membership_role_or_foreign_grant_never_substitutes_for_current_grant(workspace, fault):
    actor, clinic, member, _, create, owner = workspace
    create()
    if fault == "same-clinic-ungranted":
        other = actor + 2_000_000_000
        _add_member(clinic, other)
        assert select(other).examples == []
        return
    if fault in {"ungranted", "foreign"}:
        other = actor + 2_000_000_000
        other_clinic, other_member = seed_admin(other, role="CLINIC_OWNER")
        if fault == "foreign":
            _grant(other_clinic, other_member, "CONTRIBUTOR")
        assert select(other).examples == []
        return

    def mutate():
        with owner.begin() as connection:
            if fault == "revoked-grant":
                connection.execute(text("DELETE FROM reference_evaluation_access_grants "
                                        "WHERE membership_id=:id"), {"id": member})
            elif fault == "revoked-member":
                connection.execute(text("UPDATE clinic_users SET status='REVOKED' WHERE id=:id"),
                                   {"id": member})
            elif fault == "revoked-user":
                connection.execute(text("UPDATE users SET status='REVOKED' "
                                        "WHERE telegram_user_id=:id"), {"id": actor})
            else:
                connection.execute(text("UPDATE subscription_entitlements SET status='SUSPENDED' "
                                        "WHERE clinic_id=:id"), {"id": clinic})
    assert select(actor, mutate=mutate).examples == []


@pytest.mark.parametrize("fault", ["DRAFT", "READY_FOR_REVIEW", "CHANGES_REQUIRED", "REJECTED",
                                   "RETIRED", "expired", "purged", "hash", "identifier",
                                   "superseded", "missing-approval", "oversize"])
def test_unapproved_noncurrent_expired_or_invalid_sources_are_never_selected(workspace, fault):
    actor, _, _, _, create, owner = workspace
    case = create(approve=fault != "missing-approval")
    with owner.begin() as connection:
        if fault.isupper() or fault == "missing-approval":
            state = "APPROVED_FOR_EVALUATION" if fault == "missing-approval" else fault
            connection.execute(text("UPDATE reference_evaluation_cases SET status=:status "
                                    "WHERE id=:id"), {"status": state, "id": case})
        else:
            column, value = {
                "expired": ("content_expires_at", "2020-01-01T00:00:00+00:00"),
                "purged": ("content_purged_at", "2020-01-01T00:00:00+00:00"),
                "hash": ("scenario_sha256", "f" * 64),
                "identifier": ("scenario_text", "Синтетический контакт +7 999 123-45-67."),
                "superseded": ("version", 2),
                "oversize": ("scenario_text", "Пломба " * 1800),
            }[fault]
            statement = f"UPDATE reference_evaluation_case_versions SET {column}=:value "
            connection.execute(text(statement + "WHERE reference_case_id=:id"),
                               {"value": value, "id": case})
            if fault in {"identifier", "oversize"}:
                connection.execute(text("UPDATE reference_evaluation_case_versions "
                                        "SET scenario_sha256=:hash WHERE reference_case_id=:id"),
                                   {"hash": hashlib.sha256(str(value).encode()).hexdigest(),
                                    "id": case})
    assert select(actor).examples == []


def test_local_relevance_is_deterministic_bounded_and_does_not_fill_with_unrelated(workspace):
    actor, _, _, _, create, _ = workspace
    create("Синтетический трудовой пример: сотрудник оформляет отпуск.")
    for index in range(4):
        create(SCENARIO + f" Условие варианта {index} сохранено полностью.")
    selected = select(actor)
    assert len(selected.examples) == 3 and selected == select(actor)
    assert all("Условие варианта" in item.scenario_text for item in selected.examples)
    assert select(actor, summary="Лицензия прекращена.").examples == []


def test_total_budget_skips_whole_examples_and_keeps_smaller_complete_examples(workspace):
    actor, _, _, _, create, _ = workspace
    create(SCENARIO + " я" * 3200)
    create(SCENARIO + " б" * 3200)
    create(SCENARIO)
    result = select(actor)
    assert len(result.examples) == 2
    assert sum(len(item.scenario_text) for item in result.examples) <= 12000
    assert {len(item.scenario_text) for item in result.examples} == {len(SCENARIO),
                                                                  len(SCENARIO) + 6400}
