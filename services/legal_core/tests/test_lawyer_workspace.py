"""Real PostgreSQL regressions for lawyer workflow and chronological discussion."""

import os
import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from legal_core.database import owner_database_url, database_url
from sqlalchemy.exc import DBAPIError
from test_case_api import seed_admin, application_client, actor_headers, complete_fact_batch

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="PostgreSQL integration"
)


def seed_escalation() -> tuple[int, UUID, UUID]:
    owner = 77_000_000_000 + uuid4().int % 1_000_000_000
    clinic, member = seed_admin(owner, role="CLINIC_OWNER")
    escalation, case, policy, assessment = uuid4(), uuid4(), uuid4(), uuid4()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO cases(id,clinic_id,created_by_membership_id,status) "
                "VALUES (:id,:clinic,:member,'ANALYSIS_BLOCKED')"
            ),
            dict(id=case, clinic=clinic, member=member),
        )
        # An isolated historical policy suffices to construct an immutable routing record.
        c.execute(
            text(
                "INSERT INTO risk_policy_versions(id,policy_key,version,policy_json,"
                "content_sha256) "
                "VALUES (:id,:key,1,'{}',:sha)"
            ),
            dict(id=policy, key=uuid4().hex, sha="a" * 64),
        )
        c.execute(
            text(
                "INSERT INTO case_risk_assessments(id,clinic_id,case_id,policy_id,level,"
                "reason_codes_json,fact_snapshot_sha256,evidence_trace_sha256) "
                "VALUES (:id,:clinic,:case,:policy,'HIGH','[\"FORMAL_CLAIM_RECEIVED\"]',:sha,:sha)"
            ),
            dict(id=assessment, clinic=clinic, case=case, policy=policy, sha="b" * 64),
        )
        c.execute(
            text(
                "INSERT INTO case_escalations(id,clinic_id,case_id,case_risk_assessment_id,"
                "level,reason_codes_json) VALUES "
                "(:id,:clinic,:case,:assessment,'HIGH','[\"FORMAL_CLAIM_RECEIVED\"]')"
            ),
            dict(id=escalation, clinic=clinic, case=case, assessment=assessment),
        )
    engine.dispose()
    return owner, escalation, clinic


def test_lawyer_detail_claim_resolve_and_tenant_authorization() -> None:
    owner, escalation, clinic = seed_escalation()
    lawyer = owner + 2_000_000_000
    other = owner + 4_000_000_000
    admin = owner + 6_000_000_000
    outsider = owner + 8_000_000_000
    other_clinic, _ = seed_admin(outsider, role="CLINIC_OWNER")
    path = f"/v1/case-escalations/{escalation}"
    with application_client() as client:
        for user, role in (
            (lawyer, "CLINIC_LAWYER"),
            (other, "CLINIC_LAWYER"),
            (admin, "CLINIC_ADMIN"),
        ):
            assert (
                client.post(
                    "/v1/clinic/members",
                    headers=actor_headers(owner),
                    json={"telegramUserId": user, "role": role},
                ).status_code
                == 201
            )
        detail = client.get(path, headers=actor_headers(lawyer))
        assert detail.status_code == 200
        assert detail.json()["status"] == "REQUIRED"
        assert "facts" in detail.json() and "report" in detail.json()
        assert client.get(path, headers=actor_headers(outsider)).status_code == 404
        assert client.post(path + "/claim", headers=actor_headers(admin)).status_code in (403, 404)
        claimed = client.post(path + "/claim", headers=actor_headers(lawyer))
        assert claimed.status_code == 200
        assert claimed.json()["status"] == "IN_PROGRESS" and claimed.json()["assignedToMe"]
        assert client.post(path + "/claim", headers=actor_headers(other)).status_code == 409
        assert (
            client.post(
                path + "/resolve",
                headers=actor_headers(other),
                json={"body": "Синтетическое заключение"},
            ).status_code
            == 403
        )
        resolved = client.post(
            path + "/resolve",
            headers=actor_headers(lawyer),
            json={"body": "Синтетическое заключение"},
        )
        assert resolved.status_code == 200 and resolved.json()["status"] == "RESOLVED"
        assert (
            client.post(
                path + "/discussion", headers=actor_headers(owner), json={"body": "Новое сообщение"}
            ).status_code
            == 409
        )
        discussion = client.get(path + "/discussion", headers=actor_headers(owner)).json()
        assert discussion["items"][-1]["body"] == "Синтетическое заключение"
        assert str(escalation) not in [
            item["escalationId"]
            for item in client.get("/v1/case-escalations", headers=actor_headers(owner)).json()[
                "items"
            ]
        ]

    runtime = create_engine(database_url().set(drivername="postgresql+psycopg"))
    try:
        with runtime.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_clinic_id', :id, true)"),
                dict(id=str(other_clinic)),
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM case_escalation_workflow_events "
                        "WHERE escalation_id=:id"
                    ),
                    dict(id=escalation),
                )
                == 0
            )
        with pytest.raises(DBAPIError, match="immutable"), runtime.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_clinic_id', :id, true)"),
                dict(id=str(clinic)),
            )
            connection.execute(
                text(
                    "UPDATE case_escalation_workflow_events SET action='CLAIMED' "
                    "WHERE escalation_id=:id"
                ),
                dict(id=escalation),
            )
        with runtime.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_clinic_id', :id, true)"), dict(id=str(clinic))
            )
            assert connection.execute(
                text(
                    "SELECT action FROM case_escalation_workflow_events "
                    "WHERE escalation_id=:id ORDER BY sequence"
                ),
                dict(id=escalation),
            ).scalars().all() == ["CLAIMED", "RESOLVED"]
    finally:
        runtime.dispose()


def test_latest_discussion_page_is_chronological_and_all_history_is_reachable() -> None:
    owner, escalation, clinic = seed_escalation()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    with engine.begin() as c:
        member = c.scalar(
            text("SELECT id FROM clinic_users WHERE clinic_id=:clinic"), dict(clinic=clinic)
        )
        for i in range(125):
            c.execute(
                text(
                    "INSERT INTO case_escalation_messages(clinic_id,escalation_id,"
                    "author_membership_id,body,created_at) VALUES (:clinic,:escalation,:member,"
                    ":body, '2026-01-01'::timestamptz + :n * interval '1 second')"
                ),
                dict(
                    clinic=clinic, escalation=escalation, member=member, body=f"Сообщение {i}", n=i
                ),
            )
    engine.dispose()
    with application_client() as client:
        path = f"/v1/case-escalations/{escalation}/discussion"
        latest = client.get(path, headers=actor_headers(owner), params={"limit": 20}).json()
        assert [m["body"] for m in latest["items"]] == [f"Сообщение {i}" for i in range(105, 125)]
        seen = [m["id"] for m in latest["items"]]
        while latest["nextBefore"]:
            latest = client.get(
                path,
                headers=actor_headers(owner),
                params={"limit": 20, "before": latest["nextBefore"]},
            ).json()
            seen += [m["id"] for m in latest["items"]]
        assert len(seen) == len(set(seen)) == 125
        assert (
            client.get(
                path, headers=actor_headers(owner), params={"before": str(uuid4())}
            ).status_code
            == 422
        )


def test_escalation_queue_cursor_keeps_all_older_cards_reachable() -> None:
    owner, escalation, clinic = seed_escalation()
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    created_ids = []
    with engine.begin() as c:
        case, policy = c.execute(
            text(
                "SELECT e.case_id,r.policy_id FROM case_escalations e "
                "JOIN case_risk_assessments r ON r.id=e.case_risk_assessment_id WHERE e.id=:id"
            ),
            dict(id=escalation),
        ).one()
        for i in range(105):
            assessment, identifier = uuid4(), uuid4()
            c.execute(
                text(
                    "INSERT INTO case_risk_assessments(id,clinic_id,case_id,policy_id,level,"
                    "reason_codes_json,fact_snapshot_sha256,evidence_trace_sha256) VALUES "
                    "(:id,:clinic,:case,:policy,'HIGH','[\"FORMAL_CLAIM_RECEIVED\"]',:sha,:sha)"
                ),
                dict(id=assessment, clinic=clinic, case=case, policy=policy, sha="a" * 64),
            )
            c.execute(
                text(
                    "INSERT INTO case_escalations(id,clinic_id,case_id,case_risk_assessment_id,"
                    "level,reason_codes_json,created_at) VALUES (:id,:clinic,:case,:assessment,"
                    "'HIGH','[\"FORMAL_CLAIM_RECEIVED\"]',"
                    "'2026-01-01'::timestamptz + :n*interval '1 second')"
                ),
                dict(id=identifier, clinic=clinic, case=case, assessment=assessment, n=i),
            )
            created_ids.append(str(identifier))
    engine.dispose()
    with application_client() as client:
        params = {"limit": 20, "status": "OPEN"}
        seen = []
        while True:
            page = client.get("/v1/case-escalations", headers=actor_headers(owner), params=params)
            assert page.status_code == 200
            data = page.json()
            seen.extend(item["escalationId"] for item in data["items"])
            if not data["nextBefore"]:
                break
            params["before"] = data["nextBefore"]
        assert seen == [str(escalation), *reversed(created_ids)]


def test_approved_v2_routes_confirmed_case_without_legal_evidence(monkeypatch) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from legal_core.database import database_url
    from legal_core import risk_policy_approval
    from legal_core.risk_policy_repository import ApprovedRiskPolicyRepository

    owner = 99_000_000_000 + uuid4().int % 1_000_000_000
    clinic, _ = seed_admin(owner, role="CLINIC_OWNER")
    key = f"triage-{uuid4().hex}"
    monkeypatch.setattr(risk_policy_approval, "POLICY_KEY", key)
    original_get = ApprovedRiskPolicyRepository.get

    async def isolated_policy(self, **kwargs):
        return await original_get(self, policy_key=key)

    monkeypatch.setattr(ApprovedRiskPolicyRepository, "get", isolated_policy)
    engine = create_engine(owner_database_url().set(drivername="postgresql+psycopg"))
    with engine.begin() as c:
        c.execute(
            text("UPDATE users SET system_role='LEGAL_EDITOR' WHERE telegram_user_id=:id"),
            dict(id=owner),
        )

    async def approve_versions():
        runtime = create_async_engine(database_url())
        try:
            factory = async_sessionmaker(runtime, expire_on_commit=False)
            values = dict(
                reviewer_telegram_user_id=owner,
                high_demand_threshold_kopecks=5_000_000,
                incident_triggers_reviewed=True,
                monetary_threshold_reviewed=True,
                escalation_rules_reviewed=True,
            )
            await risk_policy_approval.approve_risk_policy(
                factory, risk_policy_approval.RiskPolicyApproval(**values)
            )
            await risk_policy_approval.approve_risk_policy(
                factory,
                risk_policy_approval.RiskPolicyApproval(
                    **values, version=2, early_triage_enabled=True, supersede_approved=True
                ),
            )
        finally:
            await runtime.dispose()

    asyncio.run(approve_versions())
    batch = complete_fact_batch()
    batch["facts"].append(
        {
            "factKey": "HOSPITALIZATION",
            "valueType": "BOOLEAN",
            "value": {"boolean": True},
            "sourceType": "USER_STATEMENT",
        }
    )
    with application_client() as client:
        created = client.post(
            "/v1/cases",
            headers=actor_headers(owner, uuid4()),
            json={"intakeSchemaVersion": "dental-case-intake.v1", "channel": "TELEGRAM"},
        )
        assert created.status_code == 201
        case_id = created.json()["id"]
        response = client.post(
            f"/v1/cases/{case_id}/facts", headers=actor_headers(owner, uuid4()), json=batch
        )
        assert response.status_code == 200
        confirmed = client.post(
            f"/v1/cases/{case_id}/intake-finalizations",
            headers=actor_headers(owner, uuid4()),
            json={},
        )
        assert confirmed.status_code == 200, confirmed.json()
        queue = client.get("/v1/case-escalations", headers=actor_headers(owner)).json()["items"]
        assert len(queue) == 1 and queue[0]["riskLevel"] == "CRITICAL"
        assert confirmed.json()["earlyEscalationId"] == queue[0]["escalationId"]
        detail = client.get(
            f"/v1/case-escalations/{queue[0]['escalationId']}", headers=actor_headers(owner)
        ).json()
        assert detail["facts"]["HOSPITALIZATION"] is True
        assert detail["caseStatus"] == "ANALYSIS_BLOCKED"
        report = client.post(
            f"/v1/cases/{case_id}/reports", headers=actor_headers(owner, uuid4()), json={}
        )
        assert report.status_code == 201
        detail = client.get(
            f"/v1/case-escalations/{queue[0]['escalationId']}", headers=actor_headers(owner)
        ).json()
        assert detail["report"]["reportId"] == report.json()["id"]
        workflow_id = uuid4()
        workflow_path = f"/v1/telegram-case-workflows/{workflow_id}/submissions"
        workflow_payload = {
            "intakeSchemaVersion": "dental-case-intake.v1",
            "locale": "ru-RU",
            "facts": batch["facts"],
        }
        workflow = client.post(workflow_path, headers=actor_headers(owner), json=workflow_payload)
        assert workflow.status_code == 201, workflow.json()
        replay = client.post(workflow_path, headers=actor_headers(owner), json=workflow_payload)
        assert replay.status_code == 200
        assert workflow.json()["case"]["earlyEscalationId"] is not None
        assert (
            replay.json()["case"]["earlyEscalationId"]
            == workflow.json()["case"]["earlyEscalationId"]
        )

    async def repeat_routing_and_completed_assessment():
        from legal_core.case_api import resolve_actor, _current_fact_rows, _domain_facts
        from legal_core.risk_persistence import ensure_early_triage, record_case_risk_assessment
        from legal_core.risk_engine import evaluate_risk

        runtime = create_async_engine(database_url())
        try:
            factory = async_sessionmaker(runtime, expire_on_commit=False)
            async with factory() as session, session.begin():
                actor = await resolve_actor(session, owner)
                facts = _domain_facts(await _current_fact_rows(session, UUID(case_id)))
                escalation = await ensure_early_triage(
                    session,
                    clinic_id=clinic,
                    case_id=UUID(case_id),
                    actor_membership_id=actor.membership_id,
                    facts=facts,
                )
                assert str(escalation) == queue[0]["escalationId"]
                policy = await ApprovedRiskPolicyRepository(session).get()
                completed = await record_case_risk_assessment(
                    session,
                    clinic_id=clinic,
                    case_id=UUID(case_id),
                    actor_membership_id=actor.membership_id,
                    policy_id=policy.id,
                    assessment=evaluate_risk(facts, policy=policy.domain, evidence_verified=True),
                    evidence_trace_sha256="e" * 64,
                )
                assert completed.escalation_id == escalation
        finally:
            await runtime.dispose()

    asyncio.run(repeat_routing_and_completed_assessment())
    with engine.begin() as c:
        policies = c.execute(
            text(
                "SELECT version,status FROM risk_policy_versions "
                "WHERE policy_key=:key ORDER BY version"
            ),
            dict(key=key),
        ).all()
        assert policies == [(1, "RETIRED"), (2, "APPROVED")]
        assert (
            c.scalar(
                text(
                    "SELECT count(*) FROM case_risk_assessments "
                    "WHERE clinic_id=:clinic AND external_draft_allowed"
                ),
                dict(clinic=clinic),
            )
            == 0
        )
    engine.dispose()
