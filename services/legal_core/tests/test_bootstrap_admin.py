import asyncio
import os

import pytest
from legal_core.bootstrap_admin import bootstrap_admin, configured_admin
from legal_core.database import database_url
from legal_core.models import (
    Clinic,
    ClinicUser,
    SubscriptionEntitlement,
    SubscriptionEntitlementEvent,
    User,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_configured_admin_is_optional_and_validated() -> None:
    assert configured_admin({}) is None
    assert configured_admin({"BOOTSTRAP_TELEGRAM_ADMIN_ID": ""}) is None
    assert configured_admin(
        {
            "BOOTSTRAP_TELEGRAM_ADMIN_ID": "7000000001",
            "BOOTSTRAP_CLINIC_NAME": "Тестовая стоматология",
        }
    ) == (7_000_000_001, "Тестовая стоматология")

    with pytest.raises(ValueError, match="positive integer"):
        configured_admin({"BOOTSTRAP_TELEGRAM_ADMIN_ID": "not-an-id"})


@pytest.mark.skipif(
    os.getenv("POSTGRES_INTEGRATION") != "1",
    reason="set POSTGRES_INTEGRATION=1 to run PostgreSQL bootstrap tests",
)
def test_bootstrap_admin_is_idempotent_and_creates_one_active_membership_and_entitlement() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        telegram_id = 8_220_260_002
        try:
            first = await bootstrap_admin(factory, telegram_id, "Bootstrap test clinic")
            second = await bootstrap_admin(factory, telegram_id, "Ignored rename")

            assert first == second
            async with factory() as session:
                clinic_id = await session.scalar(
                    select(ClinicUser.clinic_id)
                    .join(User, User.id == ClinicUser.user_id)
                    .where(User.telegram_user_id == telegram_id)
                )
                assert clinic_id is not None
                await session.execute(
                    select(func.set_config("app.current_clinic_id", str(clinic_id), True))
                )
                user_count = await session.scalar(
                    select(func.count(User.id)).where(User.telegram_user_id == telegram_id)
                )
                membership_count = await session.scalar(
                    select(func.count(ClinicUser.id))
                    .join(User, User.id == ClinicUser.user_id)
                    .where(
                        User.telegram_user_id == telegram_id,
                        ClinicUser.status == "ACTIVE",
                        ClinicUser.role == "CLINIC_OWNER",
                    )
                )
                entitlement_count = await session.scalar(
                    select(func.count(SubscriptionEntitlement.id))
                    .join(User, User.id == SubscriptionEntitlement.user_id)
                    .where(
                        User.telegram_user_id == telegram_id,
                        SubscriptionEntitlement.status == "ACTIVE",
                        SubscriptionEntitlement.plan_code == "MVP_MANUAL",
                        SubscriptionEntitlement.ends_at.is_(None),
                    )
                )
                entitlement_event_count = await session.scalar(
                    select(func.count(SubscriptionEntitlementEvent.id))
                    .join(
                        SubscriptionEntitlement,
                        SubscriptionEntitlement.id
                        == SubscriptionEntitlementEvent.entitlement_id,
                    )
                    .join(User, User.id == SubscriptionEntitlement.user_id)
                    .where(User.telegram_user_id == telegram_id)
                )
                clinic_name = await session.scalar(
                    select(Clinic.name)
                    .join(ClinicUser, ClinicUser.clinic_id == Clinic.id)
                    .join(User, User.id == ClinicUser.user_id)
                    .where(User.telegram_user_id == telegram_id)
                )

            assert user_count == 1
            assert membership_count == 1
            assert entitlement_count == 1
            assert entitlement_event_count == 1
            assert clinic_name == "Bootstrap test clinic"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
