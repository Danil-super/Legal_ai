"""Human-only, checksum-bound approval for immutable legal evidence artifacts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from legal_core.corpus_loader import (
    CorpusFragment,
    corpus_fragments_sha256,
    is_safe_rtf,
    normalized_text_sha256,
)
from legal_core.database import create_engine, create_session_factory
from legal_core.models import (
    LegalApprovalEvent,
    LegalDocument,
    LegalFragment,
    LegalSource,
    LegalVersion,
    User,
)

APPROVAL_POLICY_VERSION = "dental-legal-approval.v2"
PAID_MEDICAL_SERVICES_BOUNDARIES: dict[str, tuple[date, date]] = {
    "736": (date(2023, 9, 1), date(2026, 9, 1)),
    "659": (date(2026, 9, 1), date(2031, 9, 1)),
}


class ApprovalAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_telegram_user_id: int = Field(gt=0)
    version_id: UUID
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_fragments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_effective_from: date
    expected_effective_to: date | None
    source_is_official: bool
    official_text_compared: bool = False
    artifact_is_complete: bool
    effective_dates_verified: bool
    fragments_verified: bool

    @model_validator(mode="after")
    def require_human_attestations(self) -> ApprovalAttestation:
        if not all(
            (
                self.artifact_is_complete,
                self.effective_dates_verified,
                self.fragments_verified,
            )
        ) or (not self.source_is_official and not self.official_text_compared):
            raise ValueError("all legal-review attestations must be explicit")
        return self


class LegalApprovalRejected(ValueError):
    """A candidate is not approvable; the API maps this to a fail-closed response."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(f"approval blocked: {reason_code}")
        self.reason_code = reason_code


def _checks(attestation: ApprovalAttestation) -> dict[str, Any]:
    return {
        "sourceIsOfficial": attestation.source_is_official,
        # An official primary artifact is itself the comparison source. A third-party copy needs
        # an additional explicit comparison to that primary text before it can be approved.
        "officialTextCompared": (
            attestation.source_is_official or attestation.official_text_compared
        ),
        "artifactIsComplete": attestation.artifact_is_complete,
        "effectiveDatesVerified": attestation.effective_dates_verified,
        "fragmentsVerified": attestation.fragments_verified,
        "expectedNormalizedSha256": attestation.expected_normalized_sha256,
        "expectedFragmentsSha256": attestation.expected_fragments_sha256,
        "expectedEffectiveFrom": attestation.expected_effective_from.isoformat(),
        "expectedEffectiveTo": (
            attestation.expected_effective_to.isoformat()
            if attestation.expected_effective_to is not None
            else None
        ),
    }


async def _block_reason(
    session: AsyncSession,
    version: LegalVersion,
    source: LegalSource,
    attestation: ApprovalAttestation,
) -> str | None:
    if version.artifact_kind not in {"OFFICIAL_RAW", "THIRD_PARTY_VERIFIED_COPY"}:
        return "ARTIFACT_NOT_OFFICIAL_RAW"
    if version.artifact_kind == "OFFICIAL_RAW" and not attestation.source_is_official:
        return "OFFICIAL_SOURCE_NOT_ATTESTED"
    if (
        version.artifact_kind == "THIRD_PARTY_VERIFIED_COPY"
        and attestation.source_is_official
    ):
        return "TRUSTED_COPY_MISREPRESENTED_AS_OFFICIAL"
    if (
        version.artifact_kind == "THIRD_PARTY_VERIFIED_COPY"
        and not attestation.official_text_compared
    ):
        return "OFFICIAL_TEXT_COMPARISON_NOT_ATTESTED"
    if version.raw_sha256 != attestation.expected_sha256:
        return "EXPECTED_SHA_MISMATCH"
    if hashlib.sha256(version.raw_bytes).hexdigest() != version.raw_sha256:
        return "STORED_RAW_SHA_MISMATCH"
    if version.raw_mime_type == "application/pdf" and not version.raw_bytes.startswith(b"%PDF-"):
        return "INVALID_PDF_SIGNATURE"
    if version.raw_mime_type == "application/rtf" and not is_safe_rtf(version.raw_bytes):
        return "INVALID_RTF_SIGNATURE_OR_UNSAFE_CONTENT"
    if version.raw_size_bytes != len(version.raw_bytes):
        return "STORED_RAW_SIZE_MISMATCH"
    if version.artifact_retrieved_at is None:
        return "ARTIFACT_RETRIEVAL_TIME_MISSING"
    if version.raw_mime_type == "application/pdf" and version.artifact_page_count is None:
        return "ARTIFACT_PAGE_COUNT_MISSING"
    if version.normalization_scope != "FULL_DOCUMENT":
        return "NORMALIZATION_NOT_FULL_DOCUMENT"
    if version.normalized_sha256 != attestation.expected_normalized_sha256:
        return "EXPECTED_NORMALIZED_SHA_MISMATCH"
    if normalized_text_sha256(version.normalized_text) != version.normalized_sha256:
        return "STORED_NORMALIZED_SHA_MISMATCH"
    if version.fragments_sha256 != attestation.expected_fragments_sha256:
        return "EXPECTED_FRAGMENTS_SHA_MISMATCH"
    if version.effective_from != attestation.expected_effective_from:
        return "EFFECTIVE_FROM_MISMATCH"
    if version.effective_to != attestation.expected_effective_to:
        return "EFFECTIVE_TO_MISMATCH"
    document = await session.get(LegalDocument, version.document_id)
    if document is None:  # pragma: no cover - protected by foreign key
        return "LEGAL_DOCUMENT_MISSING"
    boundary = PAID_MEDICAL_SERVICES_BOUNDARIES.get(document.official_number or "")
    if boundary is not None and (version.effective_from, version.effective_to) != boundary:
        return "PAID_MEDICAL_SERVICES_BOUNDARY_MISMATCH"
    hostname = urlparse(version.source_url).hostname
    if hostname is None or hostname not in source.allowed_hosts:
        return "SOURCE_HOST_NOT_ALLOWLISTED"
    if source.status not in {"DRAFT", "APPROVED"}:
        return "SOURCE_STATUS_NOT_APPROVABLE"
    expected_trust_level = (
        "PRIMARY" if version.artifact_kind == "OFFICIAL_RAW" else "VERIFIED_COPY"
    )
    if source.trust_level != expected_trust_level:
        return "SOURCE_TRUST_LEVEL_MISMATCH"

    fragments = list(
        (
            await session.scalars(
                select(LegalFragment)
                .where(LegalFragment.version_id == version.id)
                .order_by(LegalFragment.ordinal)
            )
        ).all()
    )
    if not fragments:
        return "NO_FRAGMENTS"
    for fragment in fragments:
        if hashlib.sha256(fragment.fragment_text.encode()).hexdigest() != fragment.text_sha256:
            return "FRAGMENT_SHA_MISMATCH"
        if fragment.fragment_text not in version.normalized_text:
            return "FRAGMENT_NOT_IN_NORMALIZED_TEXT"
    fragment_models = [
        CorpusFragment(
            ordinal=fragment.ordinal,
            article=fragment.article,
            part=fragment.part,
            point=fragment.point,
            heading=fragment.heading,
            structural_path=fragment.structural_path,
            text=fragment.fragment_text,
        )
        for fragment in fragments
    ]
    if corpus_fragments_sha256(fragment_models) != version.fragments_sha256:
        return "STORED_FRAGMENTS_SHA_MISMATCH"
    if version.approval_state == "APPROVED":
        approved_event = await session.scalar(
            select(LegalApprovalEvent.id).where(
                LegalApprovalEvent.legal_version_id == version.id,
                LegalApprovalEvent.actor_user_id == version.approved_by,
                LegalApprovalEvent.expected_sha256 == version.raw_sha256,
                LegalApprovalEvent.decision == "APPROVED",
            )
        )
        if approved_event is None:
            return "APPROVED_EVENT_MISSING"
    newer_selection = await session.scalar(
        select(LegalVersion.id)
        .where(
            LegalVersion.document_id == version.document_id,
            LegalVersion.raw_sha256 == version.raw_sha256,
            LegalVersion.version_no > version.version_no,
        )
        .limit(1)
    )
    if newer_selection is not None:
        return "SUPERSEDED_BY_NEWER_EXTRACTION"
    return None


async def legal_approval_preflight_reason(
    session: AsyncSession,
    version: LegalVersion,
    source: LegalSource,
    attestation: ApprovalAttestation,
) -> str | None:
    """Return the immutable approval guard result without writing a decision event."""

    return await _block_reason(session, version, source, attestation)


async def _regression_checks(
    session: AsyncSession,
    version: LegalVersion,
    blocked_reason: str | None,
) -> tuple[dict[str, Any], str]:
    fragment_count = len(
        (
            await session.scalars(
                select(LegalFragment.id).where(LegalFragment.version_id == version.id)
            )
        ).all()
    )
    document = await session.get(LegalDocument, version.document_id)
    official_number = document.official_number if document is not None else None
    boundary = PAID_MEDICAL_SERVICES_BOUNDARIES.get(official_number or "")
    checks: dict[str, Any] = {
        "policyVersion": APPROVAL_POLICY_VERSION,
        "passed": blocked_reason is None,
        "reasonCode": blocked_reason,
        "rawShaMatches": hashlib.sha256(version.raw_bytes).hexdigest() == version.raw_sha256,
        "rawSizeMatches": len(version.raw_bytes) == version.raw_size_bytes,
        "normalizedShaMatches": (
            normalized_text_sha256(version.normalized_text) == version.normalized_sha256
        ),
        "fragmentsSha256": version.fragments_sha256,
        "fragmentCount": fragment_count,
        "normalizationScope": version.normalization_scope,
        "effectiveFrom": version.effective_from.isoformat(),
        "effectiveTo": version.effective_to.isoformat() if version.effective_to else None,
        "effectiveRangeValid": (
            version.effective_to is None or version.effective_to > version.effective_from
        ),
        "paidMedicalServicesBoundary": (
            {
                "officialNumber": official_number,
                "expectedFrom": boundary[0].isoformat(),
                "expectedTo": boundary[1].isoformat(),
                "matches": (version.effective_from, version.effective_to) == boundary,
            }
            if boundary is not None
            else None
        ),
    }
    canonical = json.dumps(checks, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    regression_result_sha256 = await session.scalar(
        text("SELECT legal_regression_result_sha256(CAST(:payload AS jsonb))"),
        {"payload": canonical},
    )
    if not isinstance(regression_result_sha256, str):  # pragma: no cover - DB contract
        raise RuntimeError("database did not return a legal regression digest")
    return checks, regression_result_sha256


async def _active_legal_editor(session: AsyncSession, telegram_user_id: int) -> User:
    reviewer = await session.scalar(
        select(User).where(
            User.telegram_user_id == telegram_user_id,
            User.status == "ACTIVE",
            User.system_role == "LEGAL_EDITOR",
        )
    )
    if reviewer is None:
        raise PermissionError("active LEGAL_EDITOR role is required")
    return reviewer


async def approve_legal_version_in_session(
    session: AsyncSession,
    attestation: ApprovalAttestation,
    *,
    reviewer: User | None = None,
    require_review_required: bool = False,
    record_rejected_attempt: bool = True,
    idempotency_key: UUID | None = None,
    request_sha256: str | None = None,
    approval_batch_id: UUID | None = None,
) -> LegalVersion:
    """Approve inside a caller-owned transaction while holding the version lock.

    The operational CLI deliberately retains its append-only rejected-attempt events. The public
    editor path passes ``record_rejected_attempt=False`` so an obsolete or malformed candidate
    cannot look like a human ``BLOCKED`` decision.
    """

    if (idempotency_key is None) != (request_sha256 is None):
        raise ValueError("idempotency key and request digest must be provided together")
    if request_sha256 is not None and len(request_sha256) != 64:
        raise ValueError("request digest must be a SHA-256 hexadecimal value")
    resolved_reviewer = reviewer or await _active_legal_editor(
        session, attestation.reviewer_telegram_user_id
    )
    if resolved_reviewer.telegram_user_id != attestation.reviewer_telegram_user_id:
        raise PermissionError("reviewer identity does not match attestation")

    version = await session.scalar(
        select(LegalVersion).where(LegalVersion.id == attestation.version_id).with_for_update()
    )
    if version is None:
        raise LookupError("legal version not found")
    source = await session.get(LegalSource, version.source_id)
    if source is None:  # pragma: no cover - protected by foreign key
        raise LookupError("legal source not found")

    blocked_reason = (
        "VERSION_NOT_REVIEW_REQUIRED"
        if require_review_required and version.approval_state != "REVIEW_REQUIRED"
        else await _block_reason(session, version, source, attestation)
    )
    regression_checks, regression_result_sha256 = await _regression_checks(
        session, version, blocked_reason
    )
    if blocked_reason is not None:
        if record_rejected_attempt:
            session.add(
                LegalApprovalEvent(
                    legal_version_id=version.id,
                    actor_user_id=resolved_reviewer.id,
                    decision="BLOCKED",
                    expected_sha256=attestation.expected_sha256,
                    reason_code=blocked_reason,
                    checks_json=_checks(attestation),
                    policy_version=APPROVAL_POLICY_VERSION,
                    regression_result_sha256=regression_result_sha256,
                    regression_checks_json=regression_checks,
                )
            )
            await session.flush()
        raise LegalApprovalRejected(blocked_reason)

    if version.approval_state == "APPROVED":
        return version
    if version.approval_state != "REVIEW_REQUIRED":  # defensive DB-lifecycle guard
        raise LegalApprovalRejected("VERSION_NOT_REVIEW_REQUIRED")

    session.add(
        LegalApprovalEvent(
            legal_version_id=version.id,
            actor_user_id=resolved_reviewer.id,
            decision="APPROVED",
            expected_sha256=attestation.expected_sha256,
            reason_code="HUMAN_LEGAL_REVIEW_PASSED",
            checks_json={
                **_checks(attestation),
                **({"batchId": str(approval_batch_id)} if approval_batch_id else {}),
            },
            policy_version=APPROVAL_POLICY_VERSION,
            regression_result_sha256=regression_result_sha256,
            regression_checks_json=regression_checks,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
        )
    )
    # The database approval-transition trigger requires this immutable event to exist before the
    # version row can enter APPROVED state.
    await session.flush()
    approved_at = datetime.now(UTC)
    version.regression_passed = True
    version.approval_state = "APPROVED"
    version.approved_by = resolved_reviewer.id
    version.approved_at = approved_at
    # Source approval is valid only after its reviewed version has completed the independently
    # guarded REVIEW_REQUIRED -> APPROVED transition.
    await session.flush()
    if source.status == "DRAFT":
        source.status = "APPROVED"
        source.approved_by = resolved_reviewer.id
        source.approved_at = approved_at
    return version


async def approve_legal_version(
    session_factory: async_sessionmaker[AsyncSession],
    attestation: ApprovalAttestation,
) -> UUID:
    """CLI compatibility wrapper that keeps rejected-attempt audit events durable."""

    async with session_factory() as session:
        try:
            await approve_legal_version_in_session(session, attestation)
        except LegalApprovalRejected:
            # The historical command deliberately records a rejected human attempt.  Commit that
            # append-only event before returning its failure to the operator; the Telegram API
            # uses the in-session function with ``record_rejected_attempt=False`` instead.
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
        await session.commit()
    return attestation.version_id


def _date(value: str) -> date:
    return date.fromisoformat(value)


async def _run(attestation: ApprovalAttestation) -> None:
    engine = create_engine()
    try:
        identifier = await approve_legal_version(create_session_factory(engine), attestation)
        print(f"approved legal version {identifier}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve a verified legal evidence artifact")
    parser.add_argument("--reviewer-telegram-user-id", type=int, required=True)
    parser.add_argument("--version-id", type=UUID, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-normalized-sha256", required=True)
    parser.add_argument("--expected-fragments-sha256", required=True)
    parser.add_argument("--expected-effective-from", type=_date, required=True)
    parser.add_argument("--expected-effective-to", type=_date)
    parser.add_argument("--attest-source-official", action="store_true")
    parser.add_argument("--attest-official-text-compared", action="store_true")
    parser.add_argument("--attest-artifact-complete", action="store_true", required=True)
    parser.add_argument("--attest-effective-dates", action="store_true", required=True)
    parser.add_argument("--attest-fragments", action="store_true", required=True)
    args = parser.parse_args()
    asyncio.run(
        _run(
            ApprovalAttestation(
                reviewer_telegram_user_id=args.reviewer_telegram_user_id,
                version_id=args.version_id,
                expected_sha256=args.expected_sha256,
                expected_normalized_sha256=args.expected_normalized_sha256,
                expected_fragments_sha256=args.expected_fragments_sha256,
                expected_effective_from=args.expected_effective_from,
                expected_effective_to=args.expected_effective_to,
                source_is_official=args.attest_source_official,
                official_text_compared=args.attest_official_text_compared,
                artifact_is_complete=args.attest_artifact_complete,
                effective_dates_verified=args.attest_effective_dates,
                fragments_verified=args.attest_fragments,
            )
        )
    )


if __name__ == "__main__":
    main()
