import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from legal_core.database import database_url
from legal_core.models import RiskPolicyEvent, RiskPolicyVersion, User
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL risk-policy guard tests",
)


def _policy_payload() -> tuple[dict[str, object], str]:
    payload: dict[str, object] = {
        "highDemandThresholdKopecks": 10_000_000,
        "schemaVersion": "risk-policy.v1",
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload, digest


async def _create_editor(
    factory: async_sessionmaker, *, role: str = "LEGAL_EDITOR"
) -> User:
    async with factory() as session, session.begin():
        user = User(
            telegram_user_id=int(f"83{uuid4().int % 10**8:08d}"),
            system_role=role,
            display_name="Synthetic policy reviewer",
        )
        session.add(user)
        await session.flush()
        identifier = user.id
    async with factory() as session:
        stored = await session.get(User, identifier)
        assert stored is not None
        return stored


def test_risk_policy_content_is_immutable_and_approval_requires_legal_editor() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        payload, digest = _policy_payload()
        editor = await _create_editor(factory)
        ordinary_user = await _create_editor(factory, role="CLINIC_ADMIN")
        try:
            async with factory() as session, session.begin():
                policy = RiskPolicyVersion(
                    policy_key=f"risk-policy-{uuid4().hex}",
                    version=1,
                    policy_json=payload,
                    content_sha256=digest,
                    created_by_user_id=editor.id,
                )
                session.add(policy)
                await session.flush()
                policy_id = policy.id

            async with factory() as session:
                policy = await session.get(RiskPolicyVersion, policy_id)
                assert policy is not None
                policy.policy_json = {"tampered": True}
                with pytest.raises(DBAPIError, match="risk policy content is immutable"):
                    await session.flush()
                await session.rollback()

            async with factory() as session:
                policy = await session.get(RiskPolicyVersion, policy_id)
                assert policy is not None
                session.add(
                    RiskPolicyEvent(
                        risk_policy_id=policy.id,
                        actor_user_id=ordinary_user.id,
                        decision="APPROVED",
                        expected_content_sha256=digest,
                        reason_code="SYNTHETIC_TEST",
                    )
                )
                with pytest.raises(DBAPIError, match="active LEGAL_EDITOR is required"):
                    await session.flush()
                await session.rollback()

            async with factory() as session, session.begin():
                policy = await session.get(RiskPolicyVersion, policy_id)
                assert policy is not None
                session.add(
                    RiskPolicyEvent(
                        risk_policy_id=policy.id,
                        actor_user_id=editor.id,
                        decision="APPROVED",
                        expected_content_sha256=digest,
                        reason_code="SYNTHETIC_TEST",
                    )
                )
                await session.flush()
                policy.status = "APPROVED"
                policy.approved_by_user_id = editor.id
                policy.approved_at = datetime.now(UTC)

            async with factory() as session:
                approved = await session.get(RiskPolicyVersion, policy_id)
                assert approved is not None
                assert approved.status == "APPROVED"
                assert approved.policy_json == payload
        finally:
            await engine.dispose()

    asyncio.run(scenario())
