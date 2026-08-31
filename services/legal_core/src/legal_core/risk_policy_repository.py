"""Approved-only access to immutable deterministic risk policy snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.models import RiskPolicyVersion
from legal_core.risk_engine import RiskPolicy


@dataclass(frozen=True, slots=True)
class ApprovedRiskPolicy:
    id: UUID
    domain: RiskPolicy


class ApprovedRiskPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, policy_key: str = "dental-risk") -> ApprovedRiskPolicy:
        row = await self._session.scalar(
            select(RiskPolicyVersion).where(
                RiskPolicyVersion.policy_key == policy_key,
                RiskPolicyVersion.status == "APPROVED",
            )
        )
        if row is None:
            raise LookupError("approved risk policy is not available")

        payload = row.policy_json
        if set(payload) != {"schemaVersion", "highDemandThresholdKopecks"}:
            raise ValueError("approved risk policy has an unsupported v1 shape")
        if payload.get("schemaVersion") != "risk-policy.v1":
            raise ValueError("approved risk policy has an unsupported schema version")
        threshold = payload["highDemandThresholdKopecks"]
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
            raise ValueError("approved risk policy has an invalid monetary threshold")

        return ApprovedRiskPolicy(
            id=row.id,
            domain=RiskPolicy(
                version=f"{row.policy_key}.v{row.version}",
                high_demand_threshold_kopecks=threshold,
            ),
        )
