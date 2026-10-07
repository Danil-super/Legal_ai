"""Synthetic tenant, transaction and delivery-lease proofs on disposable PostgreSQL."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from test_case_api import application_client, seed_admin

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1", reason="requires disposable PostgreSQL"
)
KEY = "synthetic-gateway-key-" + "x" * 32
HEADERS = {"X-Legal-Editor-Gateway-Key": KEY}
PREFIX = "/v1/internal/escalation-notifications"


def _seed():
    actor = 9_302_000_000 + uuid4().int % 10**8
    clinic, member = seed_admin(actor)
    lawyers = [actor + 100_000_000, actor + 200_000_000]
    engine = create_engine(owner_database_url())
    with engine.begin() as conn:
        for lawyer in lawyers:
            user_id = uuid4()
            conn.execute(
                text("INSERT INTO users(id,telegram_user_id) VALUES(:id,:tg)"),
                {"id": user_id, "tg": lawyer},
            )
            conn.execute(
                text(
                    "INSERT INTO clinic_users(clinic_id,user_id,role) "
                    "VALUES(:clinic,:user,'CLINIC_LAWYER')"
                ),
                {"clinic": clinic, "user": user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO subscription_entitlements(clinic_id,user_id,"
                    "plan_code,starts_at) VALUES(:clinic,:user,'SYNTHETIC',now())"
                ),
                {"clinic": clinic, "user": user_id},
            )
        case_id = conn.scalar(
            text(
                "INSERT INTO cases(clinic_id,created_by_membership_id,"
                "status,closed_at,retention_due_at) VALUES(:clinic,:member,"
                "'ESCALATED',now(),now()+interval '30 days') RETURNING id"
            ),
            {"clinic": clinic, "member": member},
        )
        # Production risk persistence separately demands an approved policy.
        policy_id = conn.scalar(
            text(
                "INSERT INTO risk_policy_versions(policy_key,version,"
                "policy_json,content_sha256) VALUES(:key,1,'{}',:sha) "
                "RETURNING id"
            ),
            {"key": "synthetic-" + uuid4().hex, "sha": "a" * 64},
        )
        assessment_id = conn.scalar(
            text(
                "INSERT INTO case_risk_assessments(clinic_id,case_id,"
                "policy_id,level,reason_codes_json,fact_snapshot_sha256,evidence_trace_sha256) "
                "VALUES(:clinic,:case,:policy,'CRITICAL','[]',:sha,:sha) RETURNING id"
            ),
            {"clinic": clinic, "case": case_id, "policy": policy_id, "sha": "b" * 64},
        )
        escalation = conn.scalar(
            text(
                "INSERT INTO case_escalations(clinic_id,case_id,"
                "case_risk_assessment_id,level,reason_codes_json) "
                "VALUES(:clinic,:case,:assessment,'CRITICAL','[]') RETURNING id"
            ),
            {"clinic": clinic, "case": case_id, "assessment": assessment_id},
        )
    engine.dispose()
    return clinic, case_id, escalation, lawyers


def _claim(client, escalation):
    # Other integration tests share the disposable DB. Acquire their finite pending
    # batches too; current leases remain excluded, so each pass makes progress.
    for _ in range(100):
        response = client.post(f"{PREFIX}/claims", headers=HEADERS)
        assert response.status_code == 200, response.text
        rows = response.json()["items"]
        ours = [item for item in rows if item["escalationId"] == str(escalation)]
        if ours or not rows:
            return ours
    raise AssertionError("synthetic notification queue did not drain")


def test_escalation_insert_and_outbox_roll_back_together():
    clinic, case_id, _, _ = _seed()
    engine = create_engine(owner_database_url())
    escalation = uuid4()
    with engine.connect() as conn:
        transaction = conn.begin()
        assessment = conn.scalar(
            text(
                "INSERT INTO case_risk_assessments(clinic_id,case_id,policy_id,level,"
                "reason_codes_json,"
                "fact_snapshot_sha256,evidence_trace_sha256) SELECT clinic_id,case_id,policy_id,"
                "'HIGH','[]',:sha,:sha FROM case_risk_assessments "
                "WHERE case_id=:case LIMIT 1 RETURNING id"
            ),
            {"case": case_id, "sha": "c" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO case_escalations(id,clinic_id,case_id,case_risk_assessment_id,level,"
                "reason_codes_json) VALUES(:id,:clinic,:case,:assessment,'HIGH','[]')"
            ),
            {"id": escalation, "clinic": clinic, "case": case_id, "assessment": assessment},
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM escalation_notifications WHERE escalation_id=:id"),
                {"id": escalation},
            )
            == 2
        )
        transaction.rollback()
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM escalation_notifications WHERE escalation_id=:id"),
                {"id": escalation},
            )
            == 0
        )
    engine.dispose()


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv("LEGAL_EDITOR_GATEWAY_KEY", KEY)


def test_notifications_transactionally_cover_both_active_lawyers_without_case_text():
    clinic, case_id, escalation, lawyers = _seed()
    with application_client() as client:
        items = _claim(client, escalation)
        assert {item["telegramUserId"] for item in items} == set(lawyers)
        assert all(
            item["caseId"] == str(case_id) and item["riskLevel"] == "CRITICAL" for item in items
        )
        assert set(items[0]) == {
            "notificationId",
            "leaseToken",
            "caseId",
            "escalationId",
            "riskLevel",
            "telegramUserId",
        }
        assert _claim(client, escalation) == []  # another worker cannot acquire current leases
        for item in items:
            lease = {"leaseToken": item["leaseToken"]}
            assert client.post(
                f"{PREFIX}/{item['notificationId']}/delivery-check", headers=HEADERS, json=lease
            ).json() == {"eligible": True}
            assert (
                client.post(
                    f"{PREFIX}/{item['notificationId']}/delivery-results",
                    headers=HEADERS,
                    json={**lease, "outcome": "DELIVERED"},
                ).status_code
                == 204
            )
    with application_client() as recovered:
        assert _claim(recovered, escalation) == []
    engine = create_engine(owner_database_url())
    with engine.connect() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM audit_events WHERE clinic_id=:clinic "
                    "AND action='ESCALATION_NOTIFICATION_DELIVERED'"
                ),
                {"clinic": clinic},
            )
            == 2
        )
    engine.dispose()


@pytest.mark.parametrize("event", ["CLAIMED", "REVOKED", "EXPIRED", "WRONG_ROLE"])
def test_eligibility_is_rechecked_after_worker_claim(event):
    clinic, _, escalation, _lawyers = _seed()
    with application_client() as client:
        items = _claim(client, escalation)
        item = items[0]
        engine = create_engine(owner_database_url())
        with engine.begin() as conn:
            member = conn.scalar(
                text(
                    "SELECT cu.id FROM clinic_users cu JOIN users u "
                    "ON u.id=cu.user_id WHERE cu.clinic_id=:clinic AND u.telegram_user_id=:tg"
                ),
                {"clinic": clinic, "tg": item["telegramUserId"]},
            )
            if event == "CLAIMED":
                conn.execute(
                    text(
                        "INSERT INTO case_escalation_workflow_events(clinic_id,"
                        "escalation_id,actor_membership_id,action) "
                        "VALUES(:clinic,:esc,:member,'CLAIMED')"
                    ),
                    {"clinic": clinic, "esc": escalation, "member": member},
                )
            elif event == "EXPIRED":
                conn.execute(
                    text(
                        "UPDATE subscription_entitlements SET starts_at=now()-interval "
                        "'2 days',ends_at=now()-interval '1 day' WHERE clinic_id=:clinic"
                    ),
                    {"clinic": clinic},
                )
            else:
                field = "status='REVOKED'" if event == "REVOKED" else "role='CLINIC_ADMIN'"
                conn.execute(
                    text(f"UPDATE clinic_users SET {field} WHERE id=:member"), {"member": member}
                )
        engine.dispose()
        check = client.post(
            f"{PREFIX}/{item['notificationId']}/delivery-check",
            headers=HEADERS,
            json={"leaseToken": item["leaseToken"]},
        )
        assert check.status_code == 200 and check.json() == {"eligible": False}
        assert (
            client.post(
                f"{PREFIX}/{item['notificationId']}/delivery-results",
                headers=HEADERS,
                json={"leaseToken": item["leaseToken"], "outcome": "DELIVERED"},
            ).status_code
            == 409
        )


def test_retry_survives_restart_and_stale_lease_cannot_ack():
    _, _, escalation, _ = _seed()
    with application_client() as client:
        item = _claim(client, escalation)[0]
        result = client.post(
            f"{PREFIX}/{item['notificationId']}/delivery-results",
            headers=HEADERS,
            json={"leaseToken": item["leaseToken"], "outcome": "RETRY", "retryAfterSeconds": 1},
        )
        assert result.status_code == 204
        assert _claim(client, escalation) == []
    engine = create_engine(owner_database_url())
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE escalation_notifications SET next_attempt_at=now()-interval "
                "'1 second' WHERE id=:id"
            ),
            {"id": item["notificationId"]},
        )
    engine.dispose()
    with application_client() as recovered:
        retry = _claim(recovered, escalation)[0]
        assert retry["notificationId"] == item["notificationId"]
        assert retry["leaseToken"] != item["leaseToken"]
        for token, status in [(item["leaseToken"], 409), (retry["leaseToken"], 204)]:
            response = recovered.post(
                f"{PREFIX}/{item['notificationId']}/delivery-results",
                headers=HEADERS,
                json={"leaseToken": token, "outcome": "DELIVERED"},
            )
            assert response.status_code == status


def test_expired_delivery_lease_recovers_after_worker_crash():
    _, _, escalation, _ = _seed()
    with application_client() as client:
        item = _claim(client, escalation)[0]
    engine = create_engine(owner_database_url())
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE escalation_notifications SET lease_until=now()-interval "
                "'1 second' WHERE id=:id"
            ),
            {"id": item["notificationId"]},
        )
    engine.dispose()
    with application_client() as recovered:
        retry = _claim(recovered, escalation)[0]
        assert retry["notificationId"] == item["notificationId"]
        assert retry["leaseToken"] != item["leaseToken"]
        response = recovered.post(
            f"{PREFIX}/{item['notificationId']}/delivery-check",
            headers=HEADERS,
            json={"leaseToken": item["leaseToken"]},
        )
        assert response.json() == {"eligible": False}


def test_internal_auth_and_input_contract_fail_closed():
    with application_client() as client:
        for headers in [{}, {"X-Legal-Editor-Gateway-Key": "wrong"}, {"X-Telegram-User-Id": "123"}]:
            assert client.post(f"{PREFIX}/claims", headers=headers).status_code == 403
        path = f"{PREFIX}/{uuid4()}/delivery-results"
        assert (
            client.post(
                path,
                headers=HEADERS,
                json={"leaseToken": str(uuid4()), "outcome": "DELIVERED", "clinicId": str(uuid4())},
            ).status_code
            == 422
        )
        assert (
            client.post(
                path,
                headers=HEADERS,
                json={"leaseToken": str(uuid4()), "outcome": "RETRY", "retryAfterSeconds": -1},
            ).status_code
            == 422
        )


def test_outbox_row_is_tenant_scoped_and_cross_tenant_recipient_fk_fails():
    import psycopg

    from legal_core.database import database_url

    clinic, _, escalation, _ = _seed()
    other_clinic, other_member = seed_admin(9_500_000_000 + uuid4().int % 10**8)
    engine = create_engine(database_url())
    with engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_clinic_id',:id,true)"), {"id": str(other_clinic)}
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM escalation_notifications WHERE escalation_id=:id"),
                {"id": escalation},
            )
            == 0
        )
    engine.dispose()
    with (
        psycopg.connect(
            owner_database_url().set(drivername="postgresql").render_as_string(hide_password=False)
        ) as conn,
        pytest.raises(psycopg.errors.ForeignKeyViolation),
        conn.transaction(),
    ):
        conn.execute(
            "INSERT INTO escalation_notifications(clinic_id,escalation_id,"
            "recipient_membership_id) VALUES(%s,%s,%s)",
            (clinic, escalation, other_member),
        )
