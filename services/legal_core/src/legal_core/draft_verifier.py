"""Deterministic gate for a machine-reviewed patient response draft.

A reviewer may recommend that a draft is safe to present to a clinic administrator, but Legal Core
recomputes the exact draft digest, verifies every cited claim server-side and applies risk/privacy
language gates. Passing this module only makes the text AVAILABLE AS A DRAFT; human approval and
all outbound sending remain separate and disabled.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from legal_core.pseudonymization import contains_obvious_direct_identifier
from legal_core.risk_engine import RiskAssessment, RiskLevel
from legal_core.verifier import VerificationResult


class DraftReviewVerdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    BLOCKED = "BLOCKED"


class DraftReviewReason(StrEnum):
    UNSUPPORTED_LEGAL_ASSERTION = "UNSUPPORTED_LEGAL_ASSERTION"
    UNVERIFIED_CLAIM_REFERENCE = "UNVERIFIED_CLAIM_REFERENCE"
    LIABILITY_ADMISSION = "LIABILITY_ADMISSION"
    PAYMENT_OR_OUTCOME_PROMISE = "PAYMENT_OR_OUTCOME_PROMISE"
    INAPPROPRIATE_TONE = "INAPPROPRIATE_TONE"
    OTHER_SAFETY_CONCERN = "OTHER_SAFETY_CONCERN"


@dataclass(frozen=True, slots=True)
class DraftReview:
    draft_sha256: str
    verdict: DraftReviewVerdict
    supported_claim_ids: tuple[str, ...]
    reason_code: DraftReviewReason | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.draft_sha256):
            raise ValueError("draft review requires a lowercase SHA-256 digest")
        if len(self.supported_claim_ids) != len(set(self.supported_claim_ids)):
            raise ValueError("draft review claim IDs must be unique")
        if any(not 1 <= len(value) <= 80 for value in self.supported_claim_ids):
            raise ValueError("draft review claim IDs must be between 1 and 80 characters")
        if self.verdict is DraftReviewVerdict.SUPPORTED:
            if not self.supported_claim_ids or self.reason_code is not None:
                raise ValueError("SUPPORTED draft review requires claims and no reason code")
        elif self.reason_code is None:
            raise ValueError("BLOCKED draft review requires a reason code")


@dataclass(frozen=True, slots=True)
class DraftVerification:
    available: bool
    reason_code: str | None
    draft_sha256: str


_DANGEROUS_DRAFT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "LIABILITY_ADMISSION",
        re.compile(
            r"\b(?:призна(?:ем|ём)|подтверждаем)\b.{0,80}"
            r"\b(?:вину|ответственност|нарушени)\w*",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "PAYMENT_OR_OUTCOME_PROMISE",
        re.compile(
            r"\b(?:гарантируем|обязуемся|точно|обязательно)\b.{0,100}"
            r"\b(?:верн(?:ем|ём|уть)|выплат\w*|компенс\w*)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)


def draft_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_patient_draft(
    *,
    text: str,
    review: DraftReview,
    claim_results: Mapping[str, VerificationResult],
    risk: RiskAssessment,
) -> DraftVerification:
    """Return AVAILABLE only when every independent server-side draft gate passes."""

    digest = draft_sha256(text)
    if not text.strip() or len(text) > 8_000:
        return DraftVerification(False, "DRAFT_TEXT_INVALID", digest)
    if risk.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        return DraftVerification(False, "HUMAN_LEGAL_REVIEW_REQUIRED", digest)
    if not risk.external_draft_allowed:
        return DraftVerification(False, "RISK_POLICY_BLOCKS_DRAFT", digest)
    if review.draft_sha256 != digest:
        return DraftVerification(False, "DRAFT_REVIEW_HASH_MISMATCH", digest)
    if review.verdict is not DraftReviewVerdict.SUPPORTED:
        reason = review.reason_code.value if review.reason_code else "DRAFT_REVIEW_BLOCKED"
        return DraftVerification(False, reason, digest)
    if contains_obvious_direct_identifier(text):
        return DraftVerification(False, "DRAFT_CONTAINS_DIRECT_IDENTIFIER", digest)

    for reason_code, pattern in _DANGEROUS_DRAFT_PATTERNS:
        if pattern.search(text):
            return DraftVerification(False, reason_code, digest)

    for claim_id in review.supported_claim_ids:
        if claim_results.get(claim_id) is not VerificationResult.VERIFIED:
            return DraftVerification(False, "DRAFT_REFERENCES_UNVERIFIED_CLAIM", digest)

    return DraftVerification(True, None, digest)
