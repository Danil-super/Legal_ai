"""Internal, audited provisioning of a clinic administrator's service entitlement."""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.database import create_engine, create_session_factory
from legal_core.models import ClinicUser, SubscriptionEntitlement, SubscriptionEntitlementEvent

_STATUSES = frozenset({"ACTIVE", "SUSPENDED", "CANCELLED"})
_PLAN_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")


def parse_utc_timestamp(raw: str) -> datetime:
    """Parse an explicit-offset ISO timestamp and normalize it to UTC."""

    normalized = raw.strip().replace("Z", "+00:00")
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


def _validate_input(
    *, status: str, plan_code: str, starts_at: datetime, ends_at: datetime | None
) -> tuple[str, str]:
    normalized_status = status.strip().upper()
    if normalized_status not in _STATUSES:
        raise ValueError("status must be ACTIVE, SUSPENDED or CANCELLED")
    normalized_plan = plan_code.strip().upper()
    if _PLAN_CODE.fullmatch(normalized_plan) is None:
        raise ValueError("plan_code must contain 1 to 80 uppercase letters, digits or underscores")
    if starts_at.tzinfo is None or (ends_at is not None and ends_at.tzinfo is None):
        raise ValueError("entitlement timestamps must include a timezone")
    if ends_at is not None and ends_at <= starts_at:
        raise ValueError("ends_at must be later than starts_at")
    return normalized_status, normalized_plan


async def _membership_for_provisioning(
    session: AsyncSession, membership_id: UUID
) -> ClinicUser:
    membership = await session.scalar(
        select(ClinicUser).where(ClinicUser.id == membership_id).with_for_update()
    )
    if membership is None or membership.status != "ACTIVE" or membership.role != "CLINIC_ADMIN":
        raise ValueError("membership must be an active CLINIC_ADMIN")
    await session.execute(
        select(func.set_config("app.current_clinic_id", str(membership.clinic_id), True))
    )
    return membership


async def provision_entitlement(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    membership_id: UUID,
    plan_code: str,
    status: str,
    starts_at: datetime,
    ends_at: datetime | None,
    performed_by_user_id: UUID | None = None,
) -> UUID:
    """Grant or change access without changing a customer's role or storing payment data."""

    async with session_factory() as session, session.begin():
        return await provision_entitlement_in_session(
            session,
            membership_id=membership_id,
            plan_code=plan_code,
            status=status,
            starts_at=starts_at,
            ends_at=ends_at,
            performed_by_user_id=performed_by_user_id,
        )


async def provision_entitlement_in_session(
    session: AsyncSession,
    *,
    membership_id: UUID,
    plan_code: str,
    status: str,
    starts_at: datetime,
    ends_at: datetime | None,
    performed_by_user_id: UUID | None = None,
) -> UUID:
    """Provision inside an existing transaction after the caller has authorized the action."""

    normalized_status, normalized_plan = _validate_input(
        status=status, plan_code=plan_code, starts_at=starts_at, ends_at=ends_at
    )
    starts_at = starts_at.astimezone(UTC)
    ends_at = None if ends_at is None else ends_at.astimezone(UTC)

    membership = await _membership_for_provisioning(session, membership_id)
    entitlement = await session.scalar(
        select(SubscriptionEntitlement)
        .where(
            SubscriptionEntitlement.clinic_id == membership.clinic_id,
            SubscriptionEntitlement.user_id == membership.user_id,
        )
        .with_for_update()
    )
    event_type = "GRANTED"
    if entitlement is None:
        entitlement = SubscriptionEntitlement(
            clinic_id=membership.clinic_id,
            user_id=membership.user_id,
            status=normalized_status,
            plan_code=normalized_plan,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        session.add(entitlement)
        await session.flush()
    else:
        entitlement.status = normalized_status
        entitlement.plan_code = normalized_plan
        entitlement.starts_at = starts_at
        entitlement.ends_at = ends_at
        event_type = "UPDATED" if normalized_status == "ACTIVE" else normalized_status

    session.add(
        SubscriptionEntitlementEvent(
            clinic_id=membership.clinic_id,
            entitlement_id=entitlement.id,
            event_type=event_type,
            performed_by_user_id=performed_by_user_id,
            metadata_json={
                "planCode": normalized_plan,
                "status": normalized_status,
                "startsAt": starts_at.isoformat(),
                "endsAt": None if ends_at is None else ends_at.isoformat(),
            },
        )
    )
    # Callers such as the platform-owner flow restore their own tenant context after
    # provisioning. Flush the target entitlement and its append-only event while the
    # target clinic RLS context is still active; commit may happen later in the caller.
    await session.flush()
    return entitlement.id


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grant or update one clinic administrator's paid service entitlement"
    )
    parser.add_argument("--membership-id", type=UUID, required=True)
    parser.add_argument("--plan-code", required=True)
    parser.add_argument("--status", default="ACTIVE", choices=sorted(_STATUSES))
    parser.add_argument(
        "--starts-at",
        default=datetime.now(UTC).isoformat(),
        help="ISO-8601 timestamp with timezone; defaults to the current UTC time",
    )
    parser.add_argument("--ends-at", help="optional ISO-8601 timestamp with timezone")
    parser.add_argument("--performed-by-user-id", type=UUID)
    return parser.parse_args()


async def _run() -> None:
    arguments = _arguments()
    starts_at = parse_utc_timestamp(arguments.starts_at)
    ends_at = None if arguments.ends_at is None else parse_utc_timestamp(arguments.ends_at)
    engine = create_engine()
    try:
        entitlement_id = await provision_entitlement(
            create_session_factory(engine),
            membership_id=arguments.membership_id,
            plan_code=arguments.plan_code,
            status=arguments.status,
            starts_at=starts_at,
            ends_at=ends_at,
            performed_by_user_id=arguments.performed_by_user_id,
        )
        print(f"Subscription entitlement is ready: {entitlement_id}")
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
