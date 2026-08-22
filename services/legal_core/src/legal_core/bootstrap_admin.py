"""Explicit, idempotent bootstrap for the first Telegram clinic administrator."""

import argparse
import asyncio
import os
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.database import create_engine, create_session_factory
from legal_core.models import Clinic, ClinicUser, User

DEFAULT_CLINIC_NAME = "Моя стоматология"


def configured_admin(environment: Mapping[str, str]) -> tuple[int, str] | None:
    raw_identifier = environment.get("BOOTSTRAP_TELEGRAM_ADMIN_ID", "").strip()
    if not raw_identifier:
        return None
    try:
        telegram_user_id = int(raw_identifier)
    except ValueError as exc:
        raise ValueError("BOOTSTRAP_TELEGRAM_ADMIN_ID must be a positive integer") from exc
    if telegram_user_id <= 0:
        raise ValueError("BOOTSTRAP_TELEGRAM_ADMIN_ID must be a positive integer")

    clinic_name = environment.get("BOOTSTRAP_CLINIC_NAME", DEFAULT_CLINIC_NAME).strip()
    if not 1 <= len(clinic_name) <= 200:
        raise ValueError("BOOTSTRAP_CLINIC_NAME must contain 1 to 200 characters")
    return telegram_user_id, clinic_name


async def _active_admin_memberships(
    session: AsyncSession, user_id: UUID
) -> list[ClinicUser]:
    return list(
        (
            await session.scalars(
                select(ClinicUser).where(
                    ClinicUser.user_id == user_id,
                    ClinicUser.status == "ACTIVE",
                    ClinicUser.role == "CLINIC_ADMIN",
                )
            )
        ).all()
    )


async def bootstrap_admin(
    session_factory: async_sessionmaker[AsyncSession],
    telegram_user_id: int,
    clinic_name: str,
) -> UUID:
    """Create exactly one server-owned clinic membership, or return the existing one."""

    if telegram_user_id <= 0:
        raise ValueError("telegram_user_id must be positive")
    normalized_name = clinic_name.strip()
    if not 1 <= len(normalized_name) <= 200:
        raise ValueError("clinic_name must contain 1 to 200 characters")

    async with session_factory() as session, session.begin():
        user = await session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id).with_for_update()
        )
        if user is None:
            user = User(
                telegram_user_id=telegram_user_id,
                display_name="Telegram administrator",
            )
            session.add(user)
            await session.flush()
        elif user.status != "ACTIVE":
            raise RuntimeError("configured Telegram user is not active")

        memberships = await _active_admin_memberships(session, user.id)
        if len(memberships) > 1:
            raise RuntimeError("configured Telegram user has multiple active admin memberships")
        if memberships:
            return memberships[0].id

        clinic = Clinic(name=normalized_name)
        session.add(clinic)
        await session.flush()
        membership = ClinicUser(
            clinic_id=clinic.id,
            user_id=user.id,
            role="CLINIC_ADMIN",
        )
        session.add(membership)
        await session.flush()
        return membership.id


async def _run(*, if_configured: bool) -> None:
    configuration = configured_admin(os.environ)
    if configuration is None:
        if if_configured:
            print("Telegram administrator bootstrap is not configured")
            return
        raise RuntimeError("BOOTSTRAP_TELEGRAM_ADMIN_ID is not configured")

    engine = create_engine()
    try:
        membership_id = await bootstrap_admin(
            create_session_factory(engine), configuration[0], configuration[1]
        )
        print(f"Telegram administrator membership is ready: {membership_id}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap a Telegram clinic administrator")
    parser.add_argument(
        "--if-configured",
        action="store_true",
        help="exit successfully when BOOTSTRAP_TELEGRAM_ADMIN_ID is empty",
    )
    arguments = parser.parse_args()
    asyncio.run(_run(if_configured=arguments.if_configured))


if __name__ == "__main__":
    main()
