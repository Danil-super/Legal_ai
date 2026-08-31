"""Fail-closed claim-to-evidence verification for a frozen case analysis.

The structural verifier proves that a claim only references returned, date-applicable
``APPROVED`` fragments and does not depend on unknown facts.  A second, independent semantic
review is required before a claim may become ``VERIFIED``.  This prevents a model from attaching
an arbitrary real fragment id to an unsupported statement and passing the gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from uuid import UUID

from legal_core.contracts import FactKey
from legal_core.legal_retrieval import ApprovedLegalFragment


class ClaimKind(StrEnum):
    LEGAL = "LEGAL"
    ACTION = "ACTION"


class VerificationResult(StrEnum):
    VERIFIED = "VERIFIED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INSUFFICIENT_FACTS = "INSUFFICIENT_FACTS"


class SemanticVerdict(StrEnum):
    """Result produced by a semantic reviewer that saw the claim and cited evidence text."""

    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"


@dataclass(frozen=True, slots=True)
class ProposedClaim:
    claim_id: str
    kind: ClaimKind
    text: str
    evidence_fragment_ids: tuple[UUID, ...]
    required_fact_keys: tuple[FactKey, ...] = ()

    def __post_init__(self) -> None:
        if not self.claim_id or len(self.claim_id) > 80:
            raise ValueError("claim identifier must be between 1 and 80 characters")
        if not self.text.strip() or len(self.text) > 4_000:
            raise ValueError("claim text must be between 1 and 4000 characters")
        if not self.evidence_fragment_ids:
            raise ValueError("every legal or action claim requires evidence")
        if len(self.evidence_fragment_ids) != len(set(self.evidence_fragment_ids)):
            raise ValueError("claim evidence fragment identifiers must be unique")
        if len(self.required_fact_keys) != len(set(self.required_fact_keys)):
            raise ValueError("claim required fact keys must be unique")


@dataclass(frozen=True, slots=True)
class SemanticReview:
    """Independent semantic judgement over one claim and a bounded evidence subset.

    ``reviewed_fragment_ids`` must reference fragments that were returned by Legal Core for the
    same analysis attempt.  The structural verifier re-checks this server-side; a reviewer cannot
    smuggle another source into the decision.
    """

    claim_id: str
    verdict: SemanticVerdict
    reviewed_fragment_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if not self.claim_id or len(self.claim_id) > 80:
            raise ValueError("semantic review claim identifier must be between 1 and 80 characters")
        if not self.reviewed_fragment_ids:
            raise ValueError("semantic review must cite at least one reviewed fragment")
        if len(self.reviewed_fragment_ids) != len(set(self.reviewed_fragment_ids)):
            raise ValueError("semantic review fragment identifiers must be unique")


@dataclass(frozen=True, slots=True)
class VerifiedClaim:
    claim_id: str
    result: VerificationResult
    reason_code: str | None
    verified_fragment_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class VerificationDecision:
    claims: tuple[VerifiedClaim, ...]

    @property
    def analysis_allowed(self) -> bool:
        return bool(self.claims) and all(
            claim.result is VerificationResult.VERIFIED for claim in self.claims
        )


def _is_unknown(facts: Mapping[FactKey, object], fact_key: FactKey) -> bool:
    value = facts.get(fact_key)
    return value is None or value == "UNKNOWN"


def _is_effective(fragment: ApprovedLegalFragment, as_of_date: date) -> bool:
    return fragment.effective_from <= as_of_date and (
        fragment.effective_to is None or as_of_date < fragment.effective_to
    )


def _structurally_applicable_evidence(
    claim: ProposedClaim,
    *,
    evidence_by_id: Mapping[UUID, ApprovedLegalFragment],
    facts: Mapping[FactKey, object],
    as_of_date: date,
) -> VerifiedClaim | tuple[UUID, ...]:
    for fact_key in claim.required_fact_keys:
        if _is_unknown(facts, fact_key):
            return VerifiedClaim(
                claim_id=claim.claim_id,
                result=VerificationResult.INSUFFICIENT_FACTS,
                reason_code=f"{fact_key.value}_UNKNOWN",
                verified_fragment_ids=(),
            )

    returned = [evidence_by_id.get(fragment_id) for fragment_id in claim.evidence_fragment_ids]
    if any(fragment is None for fragment in returned):
        return VerifiedClaim(
            claim_id=claim.claim_id,
            result=VerificationResult.UNSUPPORTED,
            reason_code="EVIDENCE_NOT_RETURNED",
            verified_fragment_ids=(),
        )

    applicable = tuple(
        fragment.fragment_id
        for fragment in returned
        if fragment is not None and _is_effective(fragment, as_of_date)
    )
    if not applicable:
        return VerifiedClaim(
            claim_id=claim.claim_id,
            result=VerificationResult.NOT_APPLICABLE,
            reason_code="EVIDENCE_NOT_EFFECTIVE_ON_CASE_DATE",
            verified_fragment_ids=(),
        )
    return applicable


def _verify_claim(
    claim: ProposedClaim,
    *,
    semantic_review: SemanticReview | None,
    evidence_by_id: Mapping[UUID, ApprovedLegalFragment],
    facts: Mapping[FactKey, object],
    as_of_date: date,
) -> VerifiedClaim:
    structural = _structurally_applicable_evidence(
        claim,
        evidence_by_id=evidence_by_id,
        facts=facts,
        as_of_date=as_of_date,
    )
    if isinstance(structural, VerifiedClaim):
        return structural
    applicable = structural

    if semantic_review is None:
        return VerifiedClaim(
            claim_id=claim.claim_id,
            result=VerificationResult.UNSUPPORTED,
            reason_code="SEMANTIC_REVIEW_REQUIRED",
            verified_fragment_ids=(),
        )

    reviewed = set(semantic_review.reviewed_fragment_ids)
    applicable_set = set(applicable)
    if not reviewed.issubset(applicable_set):
        return VerifiedClaim(
            claim_id=claim.claim_id,
            result=VerificationResult.UNSUPPORTED,
            reason_code="SEMANTIC_REVIEW_EVIDENCE_MISMATCH",
            verified_fragment_ids=(),
        )

    if semantic_review.verdict is SemanticVerdict.CONTRADICTED:
        return VerifiedClaim(
            claim_id=claim.claim_id,
            result=VerificationResult.CONTRADICTED,
            reason_code="SEMANTIC_REVIEW_CONTRADICTED",
            verified_fragment_ids=tuple(semantic_review.reviewed_fragment_ids),
        )
    if semantic_review.verdict is SemanticVerdict.UNSUPPORTED:
        return VerifiedClaim(
            claim_id=claim.claim_id,
            result=VerificationResult.UNSUPPORTED,
            reason_code="SEMANTIC_REVIEW_UNSUPPORTED",
            verified_fragment_ids=tuple(semantic_review.reviewed_fragment_ids),
        )

    return VerifiedClaim(
        claim_id=claim.claim_id,
        result=VerificationResult.VERIFIED,
        reason_code=None,
        verified_fragment_ids=tuple(semantic_review.reviewed_fragment_ids),
    )


def verify_claims(
    claims: Sequence[ProposedClaim],
    *,
    evidence: Sequence[ApprovedLegalFragment],
    facts: Mapping[FactKey, object],
    as_of_date: date,
    semantic_reviews: Sequence[SemanticReview] = (),
) -> VerificationDecision:
    """Verify claims structurally and semantically, failing closed on any missing gate."""

    claim_ids = [claim.claim_id for claim in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("claim identifiers must be unique")
    if not claims:
        return VerificationDecision(claims=())

    evidence_by_id = {fragment.fragment_id: fragment for fragment in evidence}
    if len(evidence_by_id) != len(evidence):
        raise ValueError("retrieved evidence fragment identifiers must be unique")

    semantic_by_id = {review.claim_id: review for review in semantic_reviews}
    if len(semantic_by_id) != len(semantic_reviews):
        raise ValueError("semantic review claim identifiers must be unique")
    unknown_review_ids = set(semantic_by_id) - set(claim_ids)
    if unknown_review_ids:
        raise ValueError("semantic reviews contain unknown claim identifiers")

    return VerificationDecision(
        claims=tuple(
            _verify_claim(
                claim,
                semantic_review=semantic_by_id.get(claim.claim_id),
                evidence_by_id=evidence_by_id,
                facts=facts,
                as_of_date=as_of_date,
            )
            for claim in claims
        )
    )
