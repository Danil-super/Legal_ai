"""Approved-only access to immutable deterministic risk policy snapshots."""

from __future__ import annotations

import hashlib
import json
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
    early_triage_enabled: bool = False


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
        guided = payload.get("schemaVersion") == "risk-policy.v3"
        early = payload.get("schemaVersion") in {"risk-policy.v2", "risk-policy.v3"}
        expected = {"schemaVersion", "highDemandThresholdKopecks"}
        if early:
            expected.add("earlyTriageEnabled")
        if guided:
            expected.add("guidedV2ExplicitSignalsEnabled")
        if set(payload) != expected:
            raise ValueError("approved risk policy has an unsupported v1 shape")
        if payload.get("schemaVersion") not in {
            "risk-policy.v1",
            "risk-policy.v2",
            "risk-policy.v3",
        }:
            raise ValueError("approved risk policy has an unsupported schema version")
        threshold = payload["highDemandThresholdKopecks"]
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
            raise ValueError("approved risk policy has an invalid monetary threshold")
        if early and payload["earlyTriageEnabled"] is not True:
            raise ValueError("early-triage policy must explicitly enable early triage")
        if guided:
            if (
                row.version < 3
                or threshold != 5_000_000
                or payload["guidedV2ExplicitSignalsEnabled"] is not True
            ):
                raise ValueError("v3 policy requires its explicit capability and fixed threshold")
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if digest != row.content_sha256:
                raise ValueError("approved v3 policy content hash does not match")

        return ApprovedRiskPolicy(
            id=row.id,
            early_triage_enabled=early,
            domain=RiskPolicy(
                version=f"{row.policy_key}.v{row.version}",
                high_demand_threshold_kopecks=threshold,
                guided_v2_explicit_signals_enabled=guided,
            ),
        )
