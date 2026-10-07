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
from legal_core.factual_safety_intake import SCREENING_VERSION
from legal_core.models import RiskPolicyEvent, RiskPolicyVersion, User
from legal_core.risk_engine import RiskPolicy
from legal_core.synthetic_factual_safety import assert_factual_safety_regressions
from legal_core.synthetic_risk_scenarios import assert_p0_synthetic_risk_regressions
from legal_core.synthetic_risk_v3 import assert_v3_synthetic_risk_regressions

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
    early_triage_enabled: bool = False
    supersede_approved: bool = False
    guided_v2_explicit_signals_enabled: bool = Field(default=False, strict=True)
    direct_v1_supersession_reviewed: bool = Field(default=False, strict=True)
    factual_safety_intake_enabled: bool = Field(default=False, strict=True)
    factual_safety_intake_reviewed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def require_explicit_review(self) -> RiskPolicyApproval:
        if self.factual_safety_intake_enabled != self.factual_safety_intake_reviewed or (
            self.factual_safety_intake_enabled and not self.guided_v2_explicit_signals_enabled
        ):
            raise ValueError("factual screening requires its reviewed guided-v2 candidate")
        if self.version == 3 and not self.guided_v2_explicit_signals_enabled:
            raise ValueError("version 3 requires the explicit guided-v2 risk contract")
        if self.early_triage_enabled and self.version < 2:
            raise ValueError("early triage requires a new policy version >= 2")
        if self.guided_v2_explicit_signals_enabled and not (
            self.version == 3
            and self.high_demand_threshold_kopecks == 5_000_000
            and self.early_triage_enabled
            and self.supersede_approved
            and self.direct_v1_supersession_reviewed
        ):
            raise ValueError("v3 requires the reviewed direct v1 transition and fixed threshold")
        if self.direct_v1_supersession_reviewed and not self.guided_v2_explicit_signals_enabled:
            raise ValueError("direct v1 supersession attestation applies only to v3")
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
    if approval.guided_v2_explicit_signals_enabled:
        payload: dict[str, object] = {
            "schemaVersion": "risk-policy.v3",
            "highDemandThresholdKopecks": approval.high_demand_threshold_kopecks,
            "earlyTriageEnabled": True,
            "guidedV2ExplicitSignalsEnabled": True,
        }
        if approval.factual_safety_intake_enabled:
            payload["factualSafetyIntakeVersion"] = SCREENING_VERSION
        return payload
    if approval.early_triage_enabled:
        return {
            "schemaVersion": "risk-policy.v2",
            "highDemandThresholdKopecks": approval.high_demand_threshold_kopecks,
            "earlyTriageEnabled": True,
        }
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
    candidate_policy = RiskPolicy(
        version=f"{POLICY_KEY}.v{approval.version}",
        high_demand_threshold_kopecks=approval.high_demand_threshold_kopecks,
    )
    assert_p0_synthetic_risk_regressions(candidate_policy)
    if approval.guided_v2_explicit_signals_enabled:
        candidate_policy = RiskPolicy(
            version=candidate_policy.version,
            high_demand_threshold_kopecks=candidate_policy.high_demand_threshold_kopecks,
            guided_v2_explicit_signals_enabled=True,
            factual_safety_intake_enabled=approval.factual_safety_intake_enabled,
        )
        assert_v3_synthetic_risk_regressions(candidate_policy)
        if approval.factual_safety_intake_enabled:
            assert_factual_safety_regressions(candidate_policy)

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
            select(RiskPolicyVersion)
            .where(
                RiskPolicyVersion.policy_key == POLICY_KEY,
                RiskPolicyVersion.status == "APPROVED",
                RiskPolicyVersion.id != policy.id,
            )
            .with_for_update()
        )
        if approval.guided_v2_explicit_signals_enabled:
            await _require_direct_v1_predecessor(session, another_approved)
        if another_approved is not None:
            if not approval.supersede_approved or another_approved.version >= policy.version:
                raise ValueError("another approved dental risk policy must be retired first")
            session.add(
                RiskPolicyEvent(
                    risk_policy_id=another_approved.id,
                    actor_user_id=reviewer.id,
                    decision="RETIRED",
                    expected_content_sha256=another_approved.content_sha256,
                    reason_code="EXPLICIT_NEW_POLICY_VERSION_ACTIVATION",
                )
            )
            await session.flush()
            another_approved.status = "RETIRED"
            await session.flush()

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


async def _require_direct_v1_predecessor(
    session: AsyncSession,
    predecessor: RiskPolicyVersion | None,
) -> None:
    expected_payload = {
        "schemaVersion": "risk-policy.v1",
        "highDemandThresholdKopecks": 5_000_000,
    }
    expected_digest = policy_content_sha256(expected_payload)
    if (
        predecessor is None
        or predecessor.version != 1
        or predecessor.policy_json != expected_payload
        or predecessor.content_sha256 != expected_digest
    ):
        raise ValueError("v3 requires the exact approved v1 predecessor at 50,000 RUB")
    event = await session.scalar(
        select(RiskPolicyEvent.id).where(
            RiskPolicyEvent.risk_policy_id == predecessor.id,
            RiskPolicyEvent.actor_user_id == predecessor.approved_by_user_id,
            RiskPolicyEvent.decision == "APPROVED",
            RiskPolicyEvent.expected_content_sha256 == expected_digest,
        )
    )
    if event is None:
        raise ValueError("v1 predecessor requires a matching immutable approval event")


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
    parser.add_argument("--early-triage-enabled", action="store_true")
    parser.add_argument("--supersede-approved", action="store_true")
    parser.add_argument("--guided-v2-explicit-signals-enabled", action="store_true")
    parser.add_argument("--direct-v1-supersession-reviewed", action="store_true")
    parser.add_argument("--factual-safety-intake-enabled", action="store_true")
    parser.add_argument("--factual-safety-intake-reviewed", action="store_true")
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
        early_triage_enabled=args.early_triage_enabled,
        supersede_approved=args.supersede_approved,
        guided_v2_explicit_signals_enabled=args.guided_v2_explicit_signals_enabled,
        direct_v1_supersession_reviewed=args.direct_v1_supersession_reviewed,
        factual_safety_intake_enabled=args.factual_safety_intake_enabled,
        factual_safety_intake_reviewed=args.factual_safety_intake_reviewed,
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
