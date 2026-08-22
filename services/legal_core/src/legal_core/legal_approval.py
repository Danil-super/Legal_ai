"""Human-only, checksum-bound approval for immutable official legal artifacts."""

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

APPROVAL_POLICY_VERSION = "dental-legal-approval.v1"
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
    artifact_is_complete: bool
    effective_dates_verified: bool
    fragments_verified: bool

    @model_validator(mode="after")
    def require_human_attestations(self) -> ApprovalAttestation:
        if not all(
            (
                self.source_is_official,
                self.artifact_is_complete,
                self.effective_dates_verified,
                self.fragments_verified,
            )
        ):
            raise ValueError("all legal-review attestations must be explicit")
        return self


def _checks(attestation: ApprovalAttestation) -> dict[str, Any]:
    return {
        "sourceIsOfficial": attestation.source_is_official,
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
    if version.artifact_kind != "OFFICIAL_RAW":
        return "ARTIFACT_NOT_OFFICIAL_RAW"
    if version.raw_sha256 != attestation.expected_sha256:
        return "EXPECTED_SHA_MISMATCH"
    if hashlib.sha256(version.raw_bytes).hexdigest() != version.raw_sha256:
        return "STORED_RAW_SHA_MISMATCH"
    if version.raw_mime_type == "application/pdf" and not version.raw_bytes.startswith(b"%PDF-"):
        return "INVALID_PDF_SIGNATURE"
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
    return None


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
    canonical = json.dumps(
        checks, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    regression_result_sha256 = await session.scalar(
        text("SELECT legal_regression_result_sha256(CAST(:payload AS jsonb))"),
        {"payload": canonical},
    )
    if not isinstance(regression_result_sha256, str):  # pragma: no cover - DB contract
        raise RuntimeError("database did not return a legal regression digest")
    return checks, regression_result_sha256


async def approve_legal_version(
    session_factory: async_sessionmaker[AsyncSession],
    attestation: ApprovalAttestation,
) -> UUID:
    blocked_reason: str | None = None
    async with session_factory() as session, session.begin():
        reviewer = await session.scalar(
            select(User).where(
                User.telegram_user_id == attestation.reviewer_telegram_user_id,
                User.status == "ACTIVE",
                User.system_role == "LEGAL_EDITOR",
            )
        )
        if reviewer is None:
            raise PermissionError("active LEGAL_EDITOR role is required")
        version = await session.scalar(
            select(LegalVersion)
            .where(LegalVersion.id == attestation.version_id)
            .with_for_update()
        )
        if version is None:
            raise LookupError("legal version not found")
        source = await session.get(LegalSource, version.source_id)
        if source is None:  # pragma: no cover - protected by foreign key
            raise LookupError("legal source not found")

        blocked_reason = await _block_reason(session, version, source, attestation)
        regression_checks, regression_result_sha256 = await _regression_checks(
            session, version, blocked_reason
        )
        decision = "BLOCKED" if blocked_reason is not None else "APPROVED"
        reason_code = blocked_reason or "HUMAN_LEGAL_REVIEW_PASSED"
        if version.approval_state != "APPROVED" or decision == "BLOCKED":
            session.add(
                LegalApprovalEvent(
                    legal_version_id=version.id,
                    actor_user_id=reviewer.id,
                    decision=decision,
                    expected_sha256=attestation.expected_sha256,
                    reason_code=reason_code,
                    checks_json=_checks(attestation),
                    policy_version=APPROVAL_POLICY_VERSION,
                    regression_result_sha256=regression_result_sha256,
                    regression_checks_json=regression_checks,
                )
            )
            # The database approval-transition trigger requires this immutable event to
            # exist before the version row can enter APPROVED state.
            await session.flush()
        if blocked_reason is None and version.approval_state != "APPROVED":
            approved_at = datetime.now(UTC)
            version.regression_passed = True
            version.approval_state = "APPROVED"
            version.approved_by = reviewer.id
            version.approved_at = approved_at
            # Source approval is valid only after its reviewed version has completed
            # the independently guarded REVIEW_REQUIRED -> APPROVED transition.
            await session.flush()
            if source.status == "DRAFT":
                source.status = "APPROVED"
                source.approved_by = reviewer.id
                source.approved_at = approved_at

    if blocked_reason is not None:
        raise ValueError(f"approval blocked: {blocked_reason}")
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
    parser = argparse.ArgumentParser(description="Approve a verified official legal artifact")
    parser.add_argument("--reviewer-telegram-user-id", type=int, required=True)
    parser.add_argument("--version-id", type=UUID, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-normalized-sha256", required=True)
    parser.add_argument("--expected-fragments-sha256", required=True)
    parser.add_argument("--expected-effective-from", type=_date, required=True)
    parser.add_argument("--expected-effective-to", type=_date)
    parser.add_argument("--attest-source-official", action="store_true", required=True)
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
                artifact_is_complete=args.attest_artifact_complete,
                effective_dates_verified=args.attest_effective_dates,
                fragments_verified=args.attest_fragments,
            )
        )
    )


if __name__ == "__main__":
    main()
