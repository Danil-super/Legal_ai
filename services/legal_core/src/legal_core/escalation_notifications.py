"""Internal, fenced notification transport; recipients and tenant scope come from the DB."""

from __future__ import annotations

import hmac
import os
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import Field
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from legal_core.case_api import ApiError
from legal_core.contracts import ContractModel
from legal_core.models import Base


class EscalationNotification(Base):
    __tablename__ = "escalation_notifications"
    __table_args__ = (
        UniqueConstraint("escalation_id", "recipient_membership_id"),
        ForeignKeyConstraint(
            ["clinic_id", "escalation_id"],
            ["case_escalations.clinic_id", "case_escalations.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["clinic_id", "recipient_membership_id"],
            ["clinic_users.clinic_id", "clinic_users.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("state IN ('PENDING','LEASED','DELIVERED','CANCELLED','UNDELIVERABLE')"),
        CheckConstraint("attempts >= 0"),
        CheckConstraint("(state='LEASED')=(lease_token IS NOT NULL AND lease_until IS NOT NULL)"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    clinic_id: Mapped[UUID]
    escalation_id: Mapped[UUID]
    recipient_membership_id: Mapped[UUID]
    state: Mapped[str] = mapped_column(String(20), server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class NotificationLease(ContractModel):
    lease_token: UUID = Field(alias="leaseToken")


class NotificationResult(NotificationLease):
    outcome: Literal["DELIVERED", "RETRY", "UNDELIVERABLE"]
    retry_after_seconds: int = Field(default=0, alias="retryAfterSeconds", ge=0, le=86400)


class NotificationItem(ContractModel):
    notification_id: UUID = Field(alias="notificationId")
    lease_token: UUID = Field(alias="leaseToken")
    case_id: UUID = Field(alias="caseId")
    escalation_id: UUID = Field(alias="escalationId")
    risk_level: Literal["HIGH", "CRITICAL"] = Field(alias="riskLevel")
    telegram_user_id: int = Field(alias="telegramUserId", gt=0)


class NotificationBatch(ContractModel):
    items: list[NotificationItem] = Field(max_length=20)


class NotificationEligibility(ContractModel):
    eligible: bool


def create_escalation_notifications_router(
    sessions: async_sessionmaker[AsyncSession],
) -> APIRouter:
    router = APIRouter(
        prefix="/v1/internal/escalation-notifications", tags=["internal-notifications"]
    )

    async def authenticate(
        key: Annotated[str | None, Header(alias="X-Legal-Editor-Gateway-Key")] = None,
    ) -> None:
        expected = os.getenv("LEGAL_EDITOR_GATEWAY_KEY", "").strip()
        if len(expected) < 32 or key is None or not hmac.compare_digest(key, expected):
            raise ApiError(status_code=403, code="INTERNAL_ACCESS_REQUIRED", message="Forbidden")

    async def session_dependency() -> Any:
        async with sessions() as session:
            yield session

    session_dep = Depends(session_dependency)
    auth_dep = Depends(authenticate)

    @router.post("/claims", response_model=NotificationBatch, dependencies=[auth_dep])
    async def claim(session: AsyncSession = session_dep) -> NotificationBatch:
        rows = (
            (await session.execute(text("SELECT * FROM public.claim_escalation_notifications()")))
            .mappings()
            .all()
        )
        response = NotificationBatch(items=[NotificationItem.model_validate(row) for row in rows])
        await session.commit()
        return response

    @router.post(
        "/{notification_id}/delivery-check",
        response_model=NotificationEligibility,
        dependencies=[auth_dep],
    )
    async def check(
        notification_id: UUID,
        payload: NotificationLease,
        session: AsyncSession = session_dep,
    ) -> NotificationEligibility:
        result = await session.scalar(
            text("SELECT public.check_escalation_notification(:id,:token)"),
            {"id": notification_id, "token": payload.lease_token},
        )
        await session.commit()
        return NotificationEligibility(eligible=result is True)

    @router.post("/{notification_id}/delivery-results", status_code=204, dependencies=[auth_dep])
    async def complete(
        notification_id: UUID,
        payload: NotificationResult,
        session: AsyncSession = session_dep,
    ) -> None:
        result = await session.scalar(
            text("SELECT public.complete_escalation_notification(:id,:token,:outcome,:delay)"),
            {
                "id": notification_id,
                "token": payload.lease_token,
                "outcome": payload.outcome,
                "delay": payload.retry_after_seconds,
            },
        )
        if result is not True:
            raise ApiError(
                status_code=409,
                code="NOTIFICATION_LEASE_EXPIRED",
                message="Notification lease is no longer current",
            )
        await session.commit()

    return router
