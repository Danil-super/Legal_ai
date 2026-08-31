"""Evidence-gated domain pipeline for one frozen dental case analysis.

This module deliberately contains no network or LLM code.  An agent/provider may propose claims
and an independent reviewer may produce semantic reviews, but Legal Core owns the final decision:
claims are verified against approved evidence first and deterministic risk evaluation runs only on
the resulting verified snapshot.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from legal_core.contracts import FactKey
from legal_core.legal_retrieval import ApprovedLegalFragment
from legal_core.risk_engine import RiskAssessment, RiskPolicy, evaluate_risk
from legal_core.verifier import (
    ProposedClaim,
    SemanticReview,
    VerificationDecision,
    verify_claims,
)


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    as_of_date: date
    evidence_trace_sha256: str
    verification: VerificationDecision
    risk: RiskAssessment

    @property
    def analysis_allowed(self) -> bool:
        return self.verification.analysis_allowed and self.risk.level.value != "UNAVAILABLE"


def evidence_trace_sha256(
    evidence: Sequence[ApprovedLegalFragment], *, as_of_date: date
) -> str:
    """Hash exactly the immutable evidence identity used for one analysis attempt."""

    identities = [
        {
            "documentId": str(fragment.document_id),
            "effectiveFrom": fragment.effective_from.isoformat(),
            "effectiveTo": (
                fragment.effective_to.isoformat() if fragment.effective_to is not None else None
            ),
            "fragmentId": str(fragment.fragment_id),
            "rawSha256": fragment.raw_sha256,
            "textSha256": fragment.text_sha256,
            "versionId": str(fragment.version_id),
        }
        for fragment in sorted(evidence, key=lambda item: str(item.fragment_id))
    ]
    payload = {
        "asOfDate": as_of_date.isoformat(),
        "evidence": identities,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def analyze_frozen_case(
    *,
    facts: Mapping[FactKey, object],
    as_of_date: date,
    evidence: Sequence[ApprovedLegalFragment],
    claims: Sequence[ProposedClaim],
    semantic_reviews: Sequence[SemanticReview],
    risk_policy: RiskPolicy,
) -> AnalysisOutcome:
    """Run the server-authoritative verifier and risk gates for one immutable input snapshot.

    The function is fail-closed by construction:

    * an empty claim set is not a successful analysis;
    * missing semantic review prevents claim verification;
    * unsupported/expired evidence prevents verification;
    * risk receives ``evidence_verified=False`` whenever verification did not fully pass.
    """

    trace_sha256 = evidence_trace_sha256(evidence, as_of_date=as_of_date)
    verification = verify_claims(
        claims,
        evidence=evidence,
        facts=facts,
        as_of_date=as_of_date,
        semantic_reviews=semantic_reviews,
    )
    risk = evaluate_risk(
        facts,
        policy=risk_policy,
        evidence_verified=verification.analysis_allowed,
    )
    return AnalysisOutcome(
        as_of_date=as_of_date,
        evidence_trace_sha256=trace_sha256,
        verification=verification,
        risk=risk,
    )
