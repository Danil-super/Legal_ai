import asyncio
import os

import pytest
from legal_core.bootstrap_admin import bootstrap_admin, configured_admin
from legal_core.database import database_url
from legal_core.models import Clinic, ClinicUser, User
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
def test_bootstrap_admin_is_idempotent_and_creates_one_active_membership() -> None:
    async def scenario() -> None:
        engine = create_async_engine(database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        telegram_id = 8_220_260_002
        try:
            first = await bootstrap_admin(factory, telegram_id, "Bootstrap test clinic")
            second = await bootstrap_admin(factory, telegram_id, "Ignored rename")

            assert first == second
            async with factory() as session:
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
                clinic_name = await session.scalar(
                    select(Clinic.name)
                    .join(ClinicUser, ClinicUser.clinic_id == Clinic.id)
                    .join(User, User.id == ClinicUser.user_id)
                    .where(User.telegram_user_id == telegram_id)
                )

            assert user_count == 1
            assert membership_count == 1
            assert clinic_name == "Bootstrap test clinic"
        finally:
            async with factory() as session, session.begin():
                user = await session.scalar(
                    select(User).where(User.telegram_user_id == telegram_id)
                )
                if user is not None:
                    memberships = list(
                        (
                            await session.scalars(
                                select(ClinicUser).where(ClinicUser.user_id == user.id)
                            )
                        ).all()
                    )
                    clinic_ids = [membership.clinic_id for membership in memberships]
                    for membership in memberships:
                        await session.delete(membership)
                    await session.delete(user)
                    for clinic_id in clinic_ids:
                        clinic = await session.get(Clinic, clinic_id)
                        if clinic is not None:
                            await session.delete(clinic)
            await engine.dispose()

    asyncio.run(scenario())
