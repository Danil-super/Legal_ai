"""Human-only approval path for immutable deterministic dental risk policies."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.database import create_engine, create_session_factory
from legal_core.models import RiskPolicyEvent, RiskPolicyVersion, User

POLICY_KEY = "dental-risk"
SCHEMA_VERSION = "risk-policy.v1"


class RiskPolicyApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_telegram_user_id: int = Field(gt=0)
    version: int = Field(default=1, ge=1)
    high_demand_threshold_kopecks: int = Field(gt=0)
    incident_triggers_reviewed: bool
    monetary_threshold_reviewed: bool
    escalation_rules_reviewed: bool

    @model_validator(mode="after")
    def require_explicit_review(self) -> RiskPolicyApproval:
        if not all(
            (
                self.incident_triggers_reviewed,
                self.monetary_threshold_reviewed,
                self.escalation_rules_reviewed,
            )
        ):
            raise ValueError("all risk-policy review attestations must be explicit")
        return self


def policy_payload(approval: RiskPolicyApproval) -> dict[str, object]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "highDemandThresholdKopecks": approval.high_demand_threshold_kopecks,
    }


def policy_content_sha256(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def approve_risk_policy(
    session_factory: async_sessionmaker[AsyncSession],
    approval: RiskPolicyApproval,
) -> UUID:
    payload = policy_payload(approval)
    digest = policy_content_sha256(payload)

    async with session_factory() as session, session.begin():
        reviewer = await session.scalar(
            select(User).where(
                User.telegram_user_id == approval.reviewer_telegram_user_id,
                User.status == "ACTIVE",
                User.system_role == "LEGAL_EDITOR",
            )
        )
        if reviewer is None:
            raise PermissionError("active LEGAL_EDITOR role is required")

        policy = await session.scalar(
            select(RiskPolicyVersion)
            .where(
                RiskPolicyVersion.policy_key == POLICY_KEY,
                RiskPolicyVersion.version == approval.version,
            )
            .with_for_update()
        )
        if policy is None:
            policy = RiskPolicyVersion(
                policy_key=POLICY_KEY,
                version=approval.version,
                status="DRAFT",
                policy_json=payload,
                content_sha256=digest,
                created_by_user_id=reviewer.id,
            )
            session.add(policy)
            await session.flush()
        elif policy.policy_json != payload or policy.content_sha256 != digest:
            raise ValueError("existing immutable risk-policy version has different content")

        if policy.status == "APPROVED":
            event = await session.scalar(
                select(RiskPolicyEvent.id).where(
                    RiskPolicyEvent.risk_policy_id == policy.id,
                    RiskPolicyEvent.actor_user_id == policy.approved_by_user_id,
                    RiskPolicyEvent.decision == "APPROVED",
                    RiskPolicyEvent.expected_content_sha256 == digest,
                )
            )
            if event is None:
                raise RuntimeError("approved risk policy has no matching approval event")
            return policy.id
        if policy.status != "DRAFT":
            raise ValueError("only a DRAFT risk policy can be approved")

        another_approved = await session.scalar(
            select(RiskPolicyVersion.id).where(
                RiskPolicyVersion.policy_key == POLICY_KEY,
                RiskPolicyVersion.status == "APPROVED",
                RiskPolicyVersion.id != policy.id,
            )
        )
        if another_approved is not None:
            raise ValueError("another approved dental risk policy must be retired first")

        session.add(
            RiskPolicyEvent(
                risk_policy_id=policy.id,
                actor_user_id=reviewer.id,
                decision="APPROVED",
                expected_content_sha256=digest,
                reason_code="HUMAN_RISK_POLICY_REVIEW_PASSED",
            )
        )
        # Database guards require the immutable human event before the status transition.
        await session.flush()
        policy.status = "APPROVED"
        policy.approved_by_user_id = reviewer.id
        policy.approved_at = datetime.now(UTC)
        await session.flush()
        return policy.id


def rubles_to_kopecks(value: str) -> int:
    try:
        amount = Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("threshold must be a decimal ruble amount") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("threshold must be positive with at most two decimal places")
    kopecks = amount * 100
    if kopecks != kopecks.to_integral_value():
        raise ValueError("threshold must resolve to whole kopecks")
    result = int(kopecks)
    if result > 100_000_000_000:
        raise ValueError("threshold is outside the supported range")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Approve Dental Legal AI risk-policy v1")
    parser.add_argument("--reviewer-telegram-id", type=int, required=True)
    parser.add_argument("--threshold-rubles", required=True)
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--incident-triggers-reviewed", action="store_true")
    parser.add_argument("--monetary-threshold-reviewed", action="store_true")
    parser.add_argument("--escalation-rules-reviewed", action="store_true")
    return parser


async def _run_cli() -> None:
    args = _parser().parse_args()
    approval = RiskPolicyApproval(
        reviewer_telegram_user_id=args.reviewer_telegram_id,
        version=args.version,
        high_demand_threshold_kopecks=rubles_to_kopecks(args.threshold_rubles),
        incident_triggers_reviewed=args.incident_triggers_reviewed,
        monetary_threshold_reviewed=args.monetary_threshold_reviewed,
        escalation_rules_reviewed=args.escalation_rules_reviewed,
    )
    engine = create_engine()
    factory = create_session_factory(engine)
    try:
        policy_id = await approve_risk_policy(factory, approval)
        print(f"Approved risk policy: {policy_id}")
        print(f"Content SHA-256: {policy_content_sha256(policy_payload(approval))}")
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run_cli())


if __name__ == "__main__":
    main()
