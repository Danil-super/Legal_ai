"""Disposable PG: human v1→v4 activation, immediate routing and no duplicate alerts."""

import asyncio
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from legal_core.database import database_url, owner_database_url
from legal_core.models import RiskPolicyEvent, RiskPolicyVersion, User
from legal_core.risk_policy_approval import approve_risk_policy
from test_case_api import actor_headers, application_client, seed_admin
from test_factual_safety_postgres import guided_data
from test_risk_policy_v3_postgres import isolate_policy_state as isolate_policy_state
from test_risk_policy_v3_postgres import prepare_policy
from test_risk_policy_v4 import v4_approval, v4_payload
from test_factual_safety_intake import screening
from telegram_gateway.case_wizard import facts_from_v2_data

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)


def activate_v4(*, continue_to_v5=False):
    reviewer, v1, _ = prepare_policy(activate=False)

    async def run():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            approval = v4_approval(reviewer_telegram_user_id=reviewer)
            v4 = await approve_risk_policy(factory, approval)
            assert await approve_risk_policy(factory, approval) == v4
            async with factory() as session:
                rows = (
                    await session.scalars(
                        select(RiskPolicyVersion).order_by(RiskPolicyVersion.version)
                    )
                ).all()
                assert [(p.version, p.status) for p in rows] == [(1, "RETIRED"), (4, "APPROVED")]
                assert rows[0].id == v1
                assert rows[0].policy_json["highDemandThresholdKopecks"] == 5_000_000
                assert rows[1].policy_json == v4_payload()
                events = (await session.scalars(select(RiskPolicyEvent))).all()
                assert len(events) == 3
            if not continue_to_v5:
                return v4
            v5_approval = v4_approval(
                reviewer_telegram_user_id=reviewer, version=5,
                direct_v1_supersession_reviewed=False, routine_service_scope_reviewed=True,
            )
            v5 = await approve_risk_policy(factory, v5_approval)
            assert await approve_risk_policy(factory, v5_approval) == v5
            async with factory() as session:
                policies = (await session.scalars(
                    select(RiskPolicyVersion).order_by(RiskPolicyVersion.version),
                )).all()
                assert [(row.version, row.status) for row in policies] == [
                    (1, "RETIRED"), (4, "RETIRED"), (5, "APPROVED"),
                ]
                assert policies[1].policy_json == v4_payload()
                assert len((await session.scalars(select(RiskPolicyEvent))).all()) == 5
            return v5
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.mark.parametrize("receipt_version", [None, 2])
def test_v5_scoped_service_submission_and_critical_alert_follow_distinct_routes(
    monkeypatch, receipt_version,
):
    activate_v4(continue_to_v5=True)
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    actor = 91_000_000_000 + uuid4().int % 1_000_000_000
    clinic, _ = seed_admin(actor)
    active_lawyers = seed_lawyers(clinic, actor)
    data = guided_data() | {
        "incomingKind": "COMPLAINT",
        "situationAreas": ["SERVICE"],
        "clinicActions": ["REBOOKED_AFTER_WAIT"],
        "healthSignals": ["UNKNOWN"],
        "safetyScreening": screening(
            healthDeteriorationReported="UNKNOWN", hospitalizationReported="UNKNOWN",
            representativeContact="UNKNOWN", authorityOrCourtDocumentReceived="UNKNOWN",
            writtenRequirementsReceived="UNKNOWN",
        ),
    }
    data.pop("affectedServices", None)
    with application_client() as client:
        draft = client.post(
            "/v1/telegram-intake-drafts",
            headers=actor_headers(actor, uuid4()),
            json={"intakeSchemaVersion": "dental-case-intake.v2"},
        )
        assert draft.status_code == 201, draft.text
        fetched = client.get(
            f"/v1/telegram-intake-drafts/{draft.json()['id']}", headers=actor_headers(actor),
        )
        assert fetched.status_code == 200 and fetched.json()["routineServiceScopeEnabled"] is True
        draft_id = draft.json()["id"]
        changed = dict(data, safetyScreening=screening(authorityOrCourtDocumentReceived="YES"))
        saved = client.put(
            f"/v1/telegram-intake-drafts/{draft_id}", headers=actor_headers(actor, uuid4()),
            json={"expectedRevision": 1, "wizardState": "SUMMARY", "draftData": changed},
        )
        assert saved.status_code == 200, saved.text
        for revision in (1, 2, None):
            stale = client.post(
                f"/v1/telegram-case-workflows/{draft_id}/submissions",
                headers=actor_headers(actor),
                json={
                    "intakeSchemaVersion": "dental-case-intake.v2", "locale": "ru-RU",
                    "facts": facts_from_v2_data(data),
                    **({"receiptVersion": 2} if receipt_version == 2 else {}),
                    **({"expectedDraftRevision": revision} if revision is not None else {}),
                },
            )
            assert stale.status_code == 409, stale.text
            assert stale.json()["error"]["code"] == "INTAKE_DRAFT_REVISION_CONFLICT"
        for urgent in (False, True):
            if urgent:
                data["safetyScreening"]["hospitalizationReported"] = "YES"
            else:
                saved = client.put(
                    f"/v1/telegram-intake-drafts/{draft_id}",
                    headers=actor_headers(actor, uuid4()),
                    json={"expectedRevision": 2, "wizardState": "SUMMARY", "draftData": data},
                )
                assert saved.status_code == 200, saved.text
            workflow = uuid4() if urgent else draft_id
            payload = {
                "intakeSchemaVersion": "dental-case-intake.v2",
                "locale": "ru-RU",
                "facts": facts_from_v2_data(data),
                **({"receiptVersion": 2} if receipt_version == 2 else {}),
            }
            submission = client.post(
                f"/v1/telegram-case-workflows/{workflow}/submissions",
                headers=actor_headers(actor),
                json=payload if urgent else payload | {"expectedDraftRevision": 3},
            )
            assert submission.status_code == 201, submission.text
            case = submission.json()["case"]
            assert bool(case["earlyEscalationId"]) == urgent
            replay = client.post(
                f"/v1/telegram-case-workflows/{workflow}/submissions",
                headers=actor_headers(actor), json=payload,
            )
            assert replay.status_code == 200 and replay.json() == submission.json()

    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT e.level, u.telegram_user_id, n.state FROM case_escalations e "
                "JOIN escalation_notifications n ON n.escalation_id=e.id "
                "JOIN clinic_users cu ON cu.id=n.recipient_membership_id "
                "JOIN users u ON u.id=cu.user_id WHERE e.clinic_id=:clinic"
            ), {"clinic": UUID(str(clinic))}).all()
            assert set(rows) == {("CRITICAL", lawyer, "PENDING") for lawyer in active_lawyers}
    finally:
        engine.dispose()


def test_v4_rejects_changed_predecessor_without_retiring_it_or_creating_v4():
    reviewer, _, _ = prepare_policy(initial_threshold=5_000_001, activate=False)

    async def run():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            with pytest.raises(ValueError, match="exact approved v1 predecessor"):
                await approve_risk_policy(factory, v4_approval(reviewer_telegram_user_id=reviewer))
            async with factory() as session:
                rows = (await session.scalars(select(RiskPolicyVersion))).all()
                assert [(p.version, p.status) for p in rows] == [(1, "APPROVED")]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_v4_requires_active_legal_editor_and_preserves_v1_on_denial():
    prepare_policy(activate=False)

    async def run():
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ordinary = 92_000_000_000 + uuid4().int % 1_000_000_000
        try:
            async with factory() as session, session.begin():
                session.add(User(telegram_user_id=ordinary))
            with pytest.raises(PermissionError, match="LEGAL_EDITOR"):
                await approve_risk_policy(factory, v4_approval(reviewer_telegram_user_id=ordinary))
            async with factory() as session:
                rows = (await session.scalars(select(RiskPolicyVersion))).all()
                assert [(p.version, p.status) for p in rows] == [(1, "APPROVED")]
        finally:
            await engine.dispose()

    asyncio.run(run())


def seed_lawyers(clinic, actor):
    engine = create_engine(owner_database_url())
    active = {actor + 3_000_000_000, actor + 4_000_000_000}
    try:
        with engine.begin() as conn:
            for tg, status in [(tg, "ACTIVE") for tg in active] + [
                (actor + 5_000_000_000, "REVOKED")
            ]:
                user = uuid4()
                conn.execute(
                    text("INSERT INTO users(id,telegram_user_id) VALUES(:id,:tg)"),
                    {"id": user, "tg": tg},
                )
                conn.execute(
                    text(
                        "INSERT INTO clinic_users(clinic_id,user_id,role,status) "
                        "VALUES(:clinic,:user,'CLINIC_LAWYER',:status)"
                    ),
                    {"clinic": clinic, "user": user, "status": status},
                )
                conn.execute(
                    text(
                        "INSERT INTO subscription_entitlements(clinic_id,user_id,plan_code,"
                        "starts_at) VALUES(:clinic,:user,'SYNTHETIC',now())"
                    ),
                    {"clinic": clinic, "user": user},
                )
    finally:
        engine.dispose()
    return active


@pytest.mark.parametrize("entrypoint", ["workflow", "finalization"])
@pytest.mark.parametrize("amount", [999_900, 1_000_000, 1_000_100])
def test_v4_confirmation_routes_at_10k_without_norms_and_replays_one_escalation(
    entrypoint,
    amount,
    monkeypatch,
):
    policy = activate_v4()
    monkeypatch.setenv("LEGAL_ANALYSIS_SAFE_STOP", "1")
    actor = 91_000_000_000 + uuid4().int % 1_000_000_000
    other = actor + 2_000_000_000
    clinic, _ = seed_admin(actor)
    seed_admin(other)
    active_lawyers = seed_lawyers(clinic, actor)
    data = guided_data() | {
        "safetyScreening": screening(
            moneyRequested="YES", amount={"amountKopecks": amount, "currency": "RUB"}
        )
    }
    facts = facts_from_v2_data(data)
    with application_client() as client:
        if entrypoint == "workflow":
            path = f"/v1/telegram-case-workflows/{uuid4()}/submissions"
            payload = {
                "intakeSchemaVersion": "dental-case-intake.v2",
                "locale": "ru-RU",
                "facts": facts,
            }
            headers = actor_headers(actor)
            first = client.post(path, headers=headers, json=payload)
            assert first.status_code == 201, first.text
            case = first.json()["case"]
        else:
            created = client.post(
                "/v1/cases",
                headers=actor_headers(actor, uuid4()),
                json={"intakeSchemaVersion": "dental-case-intake.v2", "channel": "TELEGRAM"},
            )
            assert created.status_code == 201, created.text
            case_id = created.json()["id"]
            added = client.post(
                f"/v1/cases/{case_id}/facts",
                headers=actor_headers(actor, uuid4()),
                json={
                    "questionId": "synthetic-10k",
                    "intakeSchemaVersion": "dental-case-intake.v2",
                    "facts": facts,
                },
            )
            assert added.status_code == 200, added.text
            path = f"/v1/cases/{case_id}/intake-finalizations"
            headers, payload = actor_headers(actor, uuid4()), {}
            first = client.post(path, headers=headers, json=payload)
            assert first.status_code == 200, first.text
            case = first.json()
        replay = client.post(path, headers=headers, json=payload)
        assert replay.status_code == 200 and replay.json() == first.json()
        pointer = case["earlyEscalationId"]
        assert (pointer is not None) is (amount >= 1_000_000)
        if pointer:
            assert client.get(
                f"/v1/case-escalations/{pointer}", headers=actor_headers(other)
            ).status_code in {403, 404}
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT e.level, r.policy_id FROM case_escalations e "
                    "JOIN case_risk_assessments r ON r.id=e.case_risk_assessment_id "
                    "WHERE e.clinic_id=:clinic AND e.case_id=:case"
                ),
                {"clinic": UUID(str(clinic)), "case": UUID(case["id"])},
            ).all()
            assert len(rows) == (1 if amount >= 1_000_000 else 0)
            if rows:
                assert rows[0] == ("HIGH", policy)
            notifications = conn.execute(
                text(
                    "SELECT u.telegram_user_id, n.state, "
                    "public.escalation_notification_is_eligible(n.id) "
                    "FROM escalation_notifications n "
                    "JOIN clinic_users cu ON cu.id=n.recipient_membership_id "
                    "JOIN users u ON u.id=cu.user_id "
                    "JOIN case_escalations e ON e.id=n.escalation_id "
                    "WHERE e.clinic_id=:clinic AND e.case_id=:case"
                ),
                {"clinic": UUID(str(clinic)), "case": UUID(case["id"])},
            ).all()
            assert len(notifications) == (2 if amount >= 1_000_000 else 0)
            if notifications:
                assert {n[0] for n in notifications} == active_lawyers
                assert all(n[1:] == ("PENDING", True) for n in notifications)
    finally:
        engine.dispose()
