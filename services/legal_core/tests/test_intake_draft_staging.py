"""Provisional preparation is not user consent; real PostgreSQL, synthetic inputs only."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from test_case_api import actor_headers, application_client, seed_admin
from test_intake_answer_consent import ANSWER, consent, proposed
from test_intake_preparation_acceptance import _proposal
from test_intake_preparation_lifecycle import _run, draft_owner

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")
__all__ = ["draft_owner"]


def preparation(draft_owner, kind):
    if kind == "answer":
        status = proposed(draft_owner)
        return status, consent(status)
    return _proposal(draft_owner[0], draft_owner[3])


def endpoint(kind, operation_id):
    namespace = "intake-answer-preparations" if kind == "answer" else "intake-preparations"
    return f"/v1/{namespace}/{operation_id}"


def stage(client, actor, status, request, kind):
    return client.post(endpoint(kind, status.operation_id) + "/draft-staging",
                       headers=actor_headers(actor), json=request.model_dump(by_alias=True))


def sql(statement, parameters):
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            result = connection.execute(text(statement), parameters)
            return result.scalar() if result.returns_rows else None
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["initial", "answer"])
def test_stage_is_durable_provisional_draft_only_and_response_is_strict(draft_owner, kind):
    actor, clinic, _, draft = draft_owner
    status, request = preparation(draft_owner, kind)
    original = sql("SELECT response_json FROM idempotency_records WHERE id=:id",
                   {"id": status.operation_id})
    with application_client() as client:
        response = stage(client, actor, status, request, kind)
        assert response.status_code == 200, response.text
        staged = response.json()
        assert set(staged) == {"operationId", "stagedRevision", "draft"}
        assert staged["stagedRevision"] == draft["revision"] + 1
        assert staged["draft"]["revision"] == staged["stagedRevision"]
        assert staged["draft"]["wizardState"] == draft["wizardState"]
        expected = ({**draft["draftData"], "eventSummary": ANSWER} if kind == "answer" else
                    {**draft["draftData"], "clinicActions": ["INVITED_FOR_EXAMINATION"]})
        assert staged["draft"]["draftData"] == expected
        assert stage(client, actor, status, request, kind).json() == staged
        assert client.get(endpoint(kind, status.operation_id),
                          headers=actor_headers(actor)).status_code == 409
        assert client.post(endpoint(kind, status.operation_id) + "/acceptance",
                           headers=actor_headers(actor),
                           json=request.model_dump(by_alias=True)).status_code == 409
    assert sql("SELECT response_json FROM idempotency_records WHERE id=:id",
               {"id": status.operation_id}) == original
    assert sql("SELECT count(*) FROM audit_events WHERE resource_id=:id "
               "AND action='INTAKE_PROPOSAL_STAGED'", {"id": status.operation_id}) == 1
    assert sql("SELECT count(*) FROM audit_events WHERE resource_id=:id "
               "AND action IN ('INTAKE_PREPARATION_ACCEPTED','INTAKE_ANSWER_ACCEPTED')",
               {"id": status.operation_id}) == 0
    for table in ("cases", "case_reports", "case_facts"):
        assert sql(f"SELECT count(*) FROM {table} WHERE clinic_id=:id", {"id": clinic}) == 0
    audit = sql("SELECT metadata_json FROM audit_events WHERE resource_id=:id "
                "AND action='INTAKE_PROPOSAL_STAGED'", {"id": status.operation_id})
    assert audit["requiresFinalConfirmation"] is True
    assert ANSWER not in str(audit)


@pytest.mark.parametrize("kind", ["initial", "answer"])
def test_lost_reply_replays_current_later_draft_without_extending_retention(draft_owner, kind):
    actor, _, _, draft = draft_owner
    status, request = preparation(draft_owner, kind)
    with application_client() as client:
        first = stage(client, actor, status, request, kind).json()
        later = client.put(f"/v1/telegram-intake-drafts/{draft['id']}",
                           headers=actor_headers(actor, uuid4()), json={
                               "expectedRevision": first["stagedRevision"],
                               "wizardState": "HEALTH", "draftData": {
                                   **first["draft"]["draftData"], "healthSignals": ["UNKNOWN"],
                               },
                           })
        assert later.status_code == 200, later.text
        replay = stage(client, actor, status, request, kind)
        assert replay.status_code == 200, replay.text
        assert replay.json()["draft"] == later.json()
        assert replay.json()["stagedRevision"] == first["stagedRevision"]


@pytest.mark.parametrize("kind", ["initial", "answer"])
@pytest.mark.parametrize("field", ["expectedRevision", "expectedInputSha256",
                                  "expectedProposalSha256"])
def test_wrong_selector_conflicts_before_and_after_staging(draft_owner, kind, field):
    actor = draft_owner[0]
    status, request = preparation(draft_owner, kind)
    changed = request.model_dump(by_alias=True)
    changed[field] = changed[field] + 1 if field == "expectedRevision" else "0" * 64
    with application_client() as client:
        for committed in (False, True):
            if committed:
                assert stage(client, actor, status, request, kind).status_code == 200
            rejected = client.post(endpoint(kind, status.operation_id) + "/draft-staging",
                                   headers=actor_headers(actor), json=changed)
            assert rejected.status_code == 409, rejected.text


@pytest.mark.parametrize("kind", ["initial", "answer"])
@pytest.mark.parametrize("change", ["foreign", "same_clinic", "revoked", "subscription",
                                   "expired", "archived"])
def test_replay_always_rechecks_ownership_and_live_access(draft_owner, kind, change):
    actor, clinic, membership, draft = draft_owner
    status, request = preparation(draft_owner, kind)
    with application_client() as client:
        assert stage(client, actor, status, request, kind).status_code == 200
        caller = actor
        if change in {"foreign", "same_clinic"}:
            caller = 98_000_000_000 + uuid4().int % 100_000_000
            _, other_membership = seed_admin(
                caller, entitlement_status=None if change == "same_clinic" else "ACTIVE",
            )
            if change == "same_clinic":
                sql("UPDATE clinic_users SET clinic_id=:clinic WHERE id=:id",
                    {"clinic": clinic, "id": other_membership})
                sql("INSERT INTO subscription_entitlements "
                    "(clinic_id,user_id,status,plan_code,starts_at) "
                    "SELECT clinic_id,user_id,'ACTIVE','MVP',now()-interval '1 day' "
                    "FROM clinic_users WHERE id=:id", {"id": other_membership})
        else:
            statements = {
                "revoked": "UPDATE clinic_users SET status='REVOKED' WHERE id=:membership",
                "subscription": "UPDATE subscription_entitlements SET status='SUSPENDED' "
                                "WHERE clinic_id=:clinic",
                "expired": "UPDATE telegram_intake_drafts SET "
                           "purge_after=now()-interval '1 minute' WHERE id=:draft",
                "archived": "UPDATE telegram_intake_drafts SET status='ARCHIVED' WHERE id=:draft",
            }
            sql(statements[change], {"membership": membership, "clinic": clinic,
                                     "draft": draft["id"]})
        rejected = stage(client, caller, status, request, kind)
        assert rejected.status_code in {403, 404, 410}, rejected.text


@pytest.mark.parametrize("kind", ["initial", "answer"])
@pytest.mark.parametrize("change", ["revision", "source", "proposal", "proposal_audit"])
def test_stale_or_tampered_proposal_never_stages(draft_owner, kind, change):
    status, request = preparation(draft_owner, kind)
    proposal_path = "interpretation,value" if kind == "answer" else "suggestions,clinicActions"
    replacement = '\"Другая ситуация со сколом.\"' if kind == "answer" else '["NOTHING_YET"]'
    statements = {
        "revision": "UPDATE telegram_intake_drafts SET revision=revision+1 WHERE id=:draft",
        "source": "UPDATE telegram_intake_drafts SET draft_json='{}'::jsonb WHERE id=:draft",
        "proposal": "UPDATE idempotency_records SET response_json=jsonb_set(response_json, "
                    f"'{{{proposal_path}}}', CAST(:replacement AS jsonb)) WHERE id=:operation",
        "proposal_audit": "UPDATE audit_events SET metadata_json='{}'::jsonb "
                          "WHERE resource_id=:operation AND action IN "
                          "('INTAKE_PREPARATION_PROPOSED','INTAKE_ANSWER_PROPOSED')",
    }
    sql(statements[change], {"draft": draft_owner[3]["id"], "operation": status.operation_id,
                             "replacement": replacement})
    with application_client() as client:
        rejected = stage(client, draft_owner[0], status, request, kind)
        assert rejected.status_code == 409, rejected.text
    assert sql("SELECT count(*) FROM audit_events WHERE resource_id=:id "
               "AND action='INTAKE_PROPOSAL_STAGED'", {"id": status.operation_id}) == 0


@pytest.mark.parametrize("kind", ["initial", "answer"])
@pytest.mark.parametrize("change", ["revision", "output", "hash", "duplicate"])
def test_corrupted_staging_receipt_or_current_output_fails_closed(draft_owner, kind, change):
    status, request = preparation(draft_owner, kind)
    with application_client() as client:
        assert stage(client, draft_owner[0], status, request, kind).status_code == 200
        statements = {
            "revision": "UPDATE audit_events SET metadata_json=jsonb_set(metadata_json, "
                        "'{stagedRevision}', '999'::jsonb) WHERE resource_id=:operation "
                        "AND action='INTAKE_PROPOSAL_STAGED'",
            "output": "UPDATE telegram_intake_drafts SET draft_json='{}'::jsonb WHERE id=:draft",
            "hash": "UPDATE audit_events SET metadata_json=jsonb_set(metadata_json, "
                    "'{requestSha256}', to_jsonb(repeat('0',64))) WHERE resource_id=:operation "
                    "AND action='INTAKE_PROPOSAL_STAGED'",
            "duplicate": "INSERT INTO audit_events "
                         "(id,clinic_id,actor_membership_id,action,resource_type,resource_id,"
                         "metadata_json,correlation_id) SELECT :new,clinic_id,actor_membership_id,"
                         "action,resource_type,resource_id,metadata_json,correlation_id "
                         "FROM audit_events "
                         "WHERE resource_id=:operation AND action='INTAKE_PROPOSAL_STAGED'",
        }
        sql(statements[change], {"draft": draft_owner[3]["id"], "operation": status.operation_id,
                                 "new": uuid4()})
        rejected = stage(client, draft_owner[0], status, request, kind)
        assert rejected.status_code == 409, rejected.text


@pytest.mark.parametrize("kind", ["initial", "answer"])
def test_concurrent_staging_writes_one_revision_and_one_receipt(draft_owner, kind):
    from legal_core.intake_draft_staging import stage_answer, stage_preparation

    status, request = preparation(draft_owner, kind)
    action = stage_answer if kind == "answer" else stage_preparation

    async def race(sessions):
        return await asyncio.gather(*[action(
            sessions, telegram_user_id=draft_owner[0], operation_id=status.operation_id,
            request=request,
        ) for _ in range(4)])

    results = _run(race)
    assert all(result == results[0] for result in results)
    assert results[0].staged_revision == draft_owner[3]["revision"] + 1
    assert sql("SELECT count(*) FROM audit_events WHERE resource_id=:id "
               "AND action='INTAKE_PROPOSAL_STAGED'", {"id": status.operation_id}) == 1


@pytest.mark.parametrize("kind", ["initial", "answer"])
@pytest.mark.parametrize("change", ["pending", "cancelled", "accepted", "wrong_kind"])
def test_only_current_unaccepted_proposals_can_be_staged(draft_owner, kind, change):
    actor = draft_owner[0]
    status, request = preparation(draft_owner, kind)
    with application_client() as client:
        if change == "accepted":
            accepted = client.post(endpoint(kind, status.operation_id) + "/acceptance",
                                   headers=actor_headers(actor),
                                   json=request.model_dump(by_alias=True))
            assert accepted.status_code == 200, accepted.text
        elif change == "cancelled":
            assert client.delete(endpoint(kind, status.operation_id),
                                 headers=actor_headers(actor)).status_code == 200
        elif change == "pending":
            sql("UPDATE idempotency_records SET state='IN_PROGRESS',lease_token=:token,"
                "lease_until=now()+interval '1 minute' WHERE id=:id",
                {"id": status.operation_id, "token": uuid4()})
        else:
            kind = "initial" if kind == "answer" else "answer"
        response = stage(client, actor, status, request, kind)
        assert response.status_code == 409, response.text


@pytest.mark.parametrize("kind", ["initial", "answer"])
@pytest.mark.parametrize("extra", [{"clinicId": "fake"}, {"value": "fake"},
                                  {"confirmed": True}])
def test_stage_rejects_client_side_grounding_or_consent_fields(draft_owner, kind, extra):
    status, request = preparation(draft_owner, kind)
    with application_client() as client:
        response = client.post(endpoint(kind, status.operation_id) + "/draft-staging",
                               headers=actor_headers(draft_owner[0]),
                               json={**request.model_dump(by_alias=True), **extra})
        assert response.status_code == 422, response.text
