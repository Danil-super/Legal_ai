import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from legal_core.database import database_url, owner_database_url
from legal_core.models import SubscriptionEntitlement, SubscriptionEntitlementEvent, User
from legal_core.subscription_provisioning import (
    parse_utc_timestamp,
    provision_entitlement,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_parse_utc_timestamp_requires_an_explicit_timezone() -> None:
    assert parse_utc_timestamp("2026-08-22T12:00:00Z") == datetime(
        2026, 8, 22, 12, 0, tzinfo=UTC
    )
    with pytest.raises(ValueError, match="timezone"):
        parse_utc_timestamp("2026-08-22T12:00:00")


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL subscription provisioning tests",
)
def test_provisioning_records_an_append_only_entitlement_history() -> None:
    async def scenario() -> None:
        runtime_engine = create_async_engine(database_url())
        runtime_factory = async_sessionmaker(runtime_engine, expire_on_commit=False)
        owner_engine = create_async_engine(owner_database_url())
        owner_factory = async_sessionmaker(owner_engine, expire_on_commit=False)
        telegram_id = 8_220_260_003 + uuid4().int % 100_000_000
        clinic_id = uuid4()
        user_id = uuid4()
        membership_id = uuid4()
        starts_at = datetime.now(UTC) - timedelta(minutes=1)
        try:
            async with owner_factory() as session, session.begin():
                await session.execute(
                    text("INSERT INTO clinics (id,name) VALUES (:id,:name)"),
                    {"id": clinic_id, "name": "Клиника подписки"},
                )
                await session.execute(
                    text("INSERT INTO users (id,telegram_user_id) VALUES (:id,:telegram_user_id)"),
                    {"id": user_id, "telegram_user_id": telegram_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO clinic_users (id,clinic_id,user_id,role) "
                        "VALUES (:id,:clinic_id,:user_id,'CLINIC_ADMIN')"
                    ),
                    {"id": membership_id, "clinic_id": clinic_id, "user_id": user_id},
                )

            first = await provision_entitlement(
                runtime_factory,
                membership_id=membership_id,
                plan_code="MVP_MONTHLY",
                status="ACTIVE",
                starts_at=starts_at,
                ends_at=None,
            )
            second = await provision_entitlement(
                runtime_factory,
                membership_id=membership_id,
                plan_code="MVP_MONTHLY",
                status="SUSPENDED",
                starts_at=starts_at,
                ends_at=None,
            )

            assert first == second
            async with runtime_factory() as session:
                hidden_without_tenant = await session.scalar(
                    select(SubscriptionEntitlement).where(
                        SubscriptionEntitlement.id == first,
                        SubscriptionEntitlement.clinic_id == clinic_id,
                    )
                )
                assert hidden_without_tenant is None

                await session.execute(
                    select(func.set_config("app.current_clinic_id", str(clinic_id), True))
                )
                entitlement = await session.scalar(
                    select(SubscriptionEntitlement).where(
                        SubscriptionEntitlement.id == first,
                        SubscriptionEntitlement.clinic_id == clinic_id,
                    )
                )
                event_count = await session.scalar(
                    select(func.count(SubscriptionEntitlementEvent.id)).where(
                        SubscriptionEntitlementEvent.entitlement_id == first
                    )
                )
                user = await session.get(User, user_id)

            assert entitlement is not None
            assert entitlement.status == "SUSPENDED"
            assert event_count == 2
            assert user is not None
            assert user.system_role is None
        finally:
            await runtime_engine.dispose()
            await owner_engine.dispose()

    asyncio.run(scenario())