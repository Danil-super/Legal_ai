"""Subscription/member writes preserve the single active tenant identity."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from legal_core.database import owner_database_url
from test_case_api import actor_headers, application_client, seed_admin
from test_verified_copy_analysis_e2e import isolated_database as isolated_database

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_INTEGRATION") != "1",
                                reason="requires disposable PostgreSQL")


def identity():
    return 87_000_000_000 + uuid4().int % 1_000_000_000


def memberships(target):
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            return connection.execute(text(
                "SELECT cu.id,cu.clinic_id,cu.role,cu.status FROM clinic_users cu "
                "JOIN users u ON u.id=cu.user_id WHERE u.telegram_user_id=:target "
                "ORDER BY cu.id"
            ), {"target": target}).all()
    finally:
        engine.dispose()


@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "CLINIC_OWNER", "CLINIC_LAWYER"])
def test_subscription_renewal_preserves_existing_membership(isolated_database, monkeypatch, role):
    owner, target = identity(), identity()
    seed_admin(owner)
    clinic, membership = seed_admin(target, role=role, entitlement_is_expired=True)
    monkeypatch.setenv("PLATFORM_OWNER_TELEGRAM_ID", str(owner))
    key = uuid4()
    with application_client() as client:
        granted = client.post("/v1/platform/subscription-grants",
                              headers=actor_headers(owner, key), json={"telegramUserId": target})
        replay = client.post("/v1/platform/subscription-grants",
                             headers=actor_headers(owner, key), json={"telegramUserId": target})
        actor = client.get("/v1/actor", headers=actor_headers(target))
    assert granted.status_code == 201, granted.text
    assert replay.status_code == 200 and replay.json() == granted.json()
    assert memberships(target) == [(membership, clinic, role, "ACTIVE")]
    assert actor.status_code == 200 and actor.json()["role"] == role
    engine = create_engine(owner_database_url())
    try:
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT clinic_id,plan_code,status FROM subscription_entitlements "
                "WHERE user_id=(SELECT id FROM users WHERE telegram_user_id=:target)"
            ), {"target": target}).all() == [(clinic, "MVP_MANUAL", "ACTIVE")]
            assert connection.scalar(text(
                "SELECT count(*) FROM subscription_entitlement_events "
                "WHERE clinic_id=:clinic"
            ), {"clinic": clinic}) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("role", ["CLINIC_ADMIN", "CLINIC_OWNER", "CLINIC_LAWYER"])
def test_foreign_member_add_conflicts_without_changing_identity(isolated_database, role):
    owner, target = identity(), identity()
    seed_admin(owner, role="CLINIC_OWNER")
    clinic, membership = seed_admin(target, role=role)
    with application_client() as client:
        added = client.post("/v1/clinic/members", headers=actor_headers(owner),
                            json={"telegramUserId": target, "role": "CLINIC_LAWYER"})
        actor = client.get("/v1/actor", headers=actor_headers(target))
    assert added.status_code == 409, added.text
    assert added.json()["error"]["code"] == "TARGET_MEMBERSHIP_CONFLICT"
    assert memberships(target) == [(membership, clinic, role, "ACTIVE")]
    assert actor.status_code == 200 and actor.json()["role"] == role


def test_concurrent_clinic_adds_leave_one_accessible_membership(isolated_database):
    owners, target = [identity(), identity()], identity()
    for owner in owners:
        seed_admin(owner, role="CLINIC_OWNER")
    barrier = Barrier(2)

    def add(owner):
        with application_client() as client:
            barrier.wait(timeout=10)
            return client.post("/v1/clinic/members", headers=actor_headers(owner),
                               json={"telegramUserId": target, "role": "CLINIC_LAWYER"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(add, owners))
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert len(memberships(target)) == 1
    with application_client() as client:
        assert client.get("/v1/actor", headers=actor_headers(target)).status_code == 200


def test_grant_and_clinic_add_serialize_on_same_new_target(isolated_database, monkeypatch):
    platform, owner, target = identity(), identity(), identity()
    seed_admin(platform)
    clinic, _ = seed_admin(owner, role="CLINIC_OWNER")
    monkeypatch.setenv("PLATFORM_OWNER_TELEGRAM_ID", str(platform))
    barrier = Barrier(2)

    def write(kind):
        with application_client() as client:
            barrier.wait(timeout=10)
            if kind == "grant":
                return client.post("/v1/platform/subscription-grants",
                                   headers=actor_headers(platform, uuid4()),
                                   json={"telegramUserId": target})
            return client.post("/v1/clinic/members", headers=actor_headers(owner),
                               json={"telegramUserId": target, "role": "CLINIC_LAWYER"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        grant, added = list(pool.map(write, ["grant", "add"]))
    assert grant.status_code == 201, grant.text
    assert added.status_code in {201, 409}, added.text
    rows = memberships(target)
    assert len(rows) == 1
    if added.status_code == 201:
        assert rows[0][1:3] == (clinic, "CLINIC_LAWYER")
    with application_client() as client:
        assert client.get("/v1/actor", headers=actor_headers(target)).status_code == 200


def test_same_clinic_member_update_and_owner_protection(isolated_database):
    owner, target = identity(), identity()
    clinic, _ = seed_admin(owner, role="CLINIC_OWNER")
    with application_client() as client:
        first = client.post("/v1/clinic/members", headers=actor_headers(owner),
                            json={"telegramUserId": target, "role": "CLINIC_ADMIN"})
        assert first.status_code == 201, first.text
        membership = memberships(target)[0][0]
        updated = client.post("/v1/clinic/members", headers=actor_headers(owner),
                              json={"telegramUserId": target, "role": "CLINIC_LAWYER"})
        protected = client.post("/v1/clinic/members", headers=actor_headers(owner),
                                json={"telegramUserId": owner, "role": "CLINIC_ADMIN"})
    assert updated.status_code == 201, updated.text
    assert memberships(target) == [(membership, clinic, "CLINIC_LAWYER", "ACTIVE")]
    assert protected.status_code == 409
    assert protected.json()["error"]["code"] == "OWNER_ROLE_PROTECTED"


@pytest.mark.parametrize("writer", ["grant", "add"])
def test_writer_rechecks_membership_after_waiting_on_target_user(
    isolated_database, monkeypatch, writer,
):
    import time

    owner, target = identity(), identity()
    seed_admin(owner, role="CLINIC_OWNER")
    clinic, membership = seed_admin(target, role="CLINIC_LAWYER")
    monkeypatch.setenv("PLATFORM_OWNER_TELEGRAM_ID", str(owner))
    engine = create_engine(owner_database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE clinic_users SET status='INACTIVE' WHERE id=:id"),
                               {"id": membership})
        with application_client() as client, engine.connect() as blocker:
            blocker.execute(text("SELECT id FROM users WHERE telegram_user_id=:target FOR UPDATE"),
                            {"target": target})
            blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
            with ThreadPoolExecutor(max_workers=1) as pool:
                path = ("/v1/platform/subscription-grants" if writer == "grant"
                        else "/v1/clinic/members")
                payload = {"telegramUserId": target}
                if writer == "add":
                    payload["role"] = "CLINIC_LAWYER"
                request = pool.submit(client.post, path, headers=actor_headers(owner, uuid4()),
                                      json=payload)
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        with engine.connect() as observer:
                            waiting = observer.scalar(text(
                                "SELECT count(*) FROM pg_stat_activity "
                                "WHERE :pid=ANY(pg_blocking_pids(pid))"
                            ), {"pid": blocker_pid})
                        if waiting:
                            break
                        assert time.monotonic() < deadline, "writer did not wait on target User"
                        time.sleep(0.02)
                    blocker.execute(text("UPDATE clinic_users SET status='ACTIVE' WHERE id=:id"),
                                    {"id": membership})
                    blocker.commit()
                finally:
                    blocker.rollback()
                response = request.result(timeout=10)
            assert response.status_code == (201 if writer == "grant" else 409), response.text
        assert memberships(target) == [(membership, clinic, "CLINIC_LAWYER", "ACTIVE")]
    finally:
        engine.dispose()
