"""Synthetic approval contracts for ADR 0075; no real legal corpus or database."""

import asyncio
import hashlib
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from legal_core.corpus_loader import (
    CorpusFragment,
    corpus_fragments_sha256,
    normalized_text_sha256,
)
from legal_core.legal_approval import ApprovalAttestation, legal_approval_preflight_reason
from legal_core.models import LegalDocument, LegalFragment, LegalSource, LegalVersion

COPY_VALID_FROM = date(2026, 10, 8)
VERSION_ID = UUID("00000000-0000-0000-0000-000000000075")
DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000076")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000000077")


def _attestation_payload(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reviewer_telegram_user_id": 1,
        "version_id": VERSION_ID,
        "expected_sha256": "a" * 64,
        "expected_normalized_sha256": "b" * 64,
        "expected_fragments_sha256": "c" * 64,
        "expected_effective_from": COPY_VALID_FROM,
        "expected_effective_to": None,
        "source_is_official": False,
        "official_text_compared": True,
        "artifact_is_complete": True,
        "effective_dates_verified": False,
        "fragments_verified": True,
        "current_copy_confirmed": True,
        "extraction_limits_understood": True,
    }
    return {**payload, **changes}


def test_current_copy_attestation_accepts_explicit_copy_checks_without_invented_dates() -> None:
    attestation = ApprovalAttestation.model_validate(_attestation_payload())

    assert attestation.effective_dates_verified is False
    assert attestation.expected_effective_from == COPY_VALID_FROM
    assert attestation.model_dump()["current_copy_confirmed"] is True
    assert attestation.model_dump()["extraction_limits_understood"] is True


@pytest.mark.parametrize(
    "field",
    [
        "current_copy_confirmed",
        "extraction_limits_understood",
        "artifact_is_complete",
        "fragments_verified",
        "official_text_compared",
    ],
)
def test_current_copy_attestation_rejects_a_missing_human_check(field: str) -> None:
    with pytest.raises(ValidationError):
        ApprovalAttestation.model_validate(_attestation_payload(**{field: False}))


@pytest.mark.parametrize("field", ["current_copy_confirmed", "extraction_limits_understood"])
def test_current_copy_attestation_does_not_infer_an_omitted_copy_check(field: str) -> None:
    payload = _attestation_payload()
    del payload[field]

    with pytest.raises(ValidationError):
        ApprovalAttestation.model_validate(payload)


def test_legacy_attestation_keeps_copy_flags_false_by_default() -> None:
    payload = _attestation_payload(effective_dates_verified=True)
    del payload["current_copy_confirmed"]
    del payload["extraction_limits_understood"]

    attestation = ApprovalAttestation.model_validate(payload)

    assert attestation.model_dump().get("current_copy_confirmed") is False
    assert attestation.model_dump().get("extraction_limits_understood") is False


def test_legacy_attestation_still_requires_verified_effective_dates() -> None:
    payload = _attestation_payload()
    del payload["current_copy_confirmed"]
    del payload["extraction_limits_understood"]

    with pytest.raises(ValidationError, match="all legal-review attestations"):
        ApprovalAttestation.model_validate(payload)


class FakeAsyncSession:
    """Read-only rows needed by approval preflight, without an approval ledger write."""

    def __init__(self, document: LegalDocument, fragments: list[LegalFragment]) -> None:
        self.document = document
        self.fragments = fragments

    async def get(self, model: type[Any], key: UUID) -> LegalDocument:
        assert model is LegalDocument
        assert key == self.document.id
        return self.document

    async def scalars(self, statement: Any) -> Any:
        return SimpleNamespace(all=lambda: self.fragments)

    async def scalar(self, statement: Any) -> None:
        # REVIEW_REQUIRED has no approval event and no newer extraction.
        return None


def _candidate() -> tuple[FakeAsyncSession, LegalVersion, LegalSource, ApprovalAttestation]:
    raw = b"{\\rtf1\\ansi Synthetic current legal copy.}"
    fragment_text = "Synthetic complete text fragment without a formula dependency."
    normalized = f"Synthetic heading. {fragment_text}"
    fragment_model = CorpusFragment(
        ordinal=1,
        article=None,
        part=None,
        point="1",
        heading=None,
        structural_path="point:1",
        text=fragment_text,
    )
    document = LegalDocument(
        id=DOCUMENT_ID,
        canonical_key="synthetic-current-copy",
        jurisdiction="RU",
        document_type="SYNTHETIC_TEST",
        title="Synthetic unnumbered current copy",
        issuer=None,
        official_number=None,
        adoption_date=None,
    )
    source = LegalSource(
        id=SOURCE_ID,
        source_key="synthetic-copy-source",
        revision=1,
        display_name="Synthetic verified copy source",
        base_url="https://example.test",
        trust_level="VERIFIED_COPY",
        allowed_hosts=["example.test"],
        status="DRAFT",
    )
    version = LegalVersion(
        id=VERSION_ID,
        document_id=DOCUMENT_ID,
        source_id=SOURCE_ID,
        version_no=1,
        source_external_id="synthetic-current-copy",
        source_url="https://example.test/synthetic-current-copy",
        publication_date=None,
        version_date=None,
        effective_from=COPY_VALID_FROM,
        effective_to=None,
        approval_state="REVIEW_REQUIRED",
        artifact_kind="THIRD_PARTY_VERIFIED_COPY",
        raw_bytes=raw,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        raw_size_bytes=len(raw),
        raw_mime_type="application/rtf",
        artifact_retrieved_at=datetime(2026, 10, 8, tzinfo=UTC),
        artifact_page_count=None,
        normalized_text=normalized,
        normalized_sha256=normalized_text_sha256(normalized),
        fragments_sha256=corpus_fragments_sha256([fragment_model]),
        normalization_scope="TEXT_LAYER",
        parser_version="synthetic-current-copy.v1",
    )
    # Set new metadata after construction so old code reaches its existing guard in RED.
    version.date_basis = "LAWYER_CURRENT_COPY"
    version.extraction_limitations = ["Graphics and formula blocks are excluded."]
    fragment = LegalFragment(
        version_id=VERSION_ID,
        ordinal=1,
        article=None,
        part=None,
        point="1",
        heading=None,
        structural_path="point:1",
        fragment_text=fragment_text,
        text_sha256=hashlib.sha256(fragment_text.encode()).hexdigest(),
    )
    # The schema has separate tests above. This double isolates preflight behavior
    # even while the old ApprovalAttestation rejects the newly introduced fields.
    attestation = cast(
        ApprovalAttestation,
        SimpleNamespace(
            **_attestation_payload(
                expected_sha256=version.raw_sha256,
                expected_normalized_sha256=version.normalized_sha256,
                expected_fragments_sha256=version.fragments_sha256,
            )
        ),
    )
    return FakeAsyncSession(document, [fragment]), version, source, attestation


def _reason(
    session: FakeAsyncSession,
    version: LegalVersion,
    source: LegalSource,
    attestation: ApprovalAttestation,
) -> str | None:
    return asyncio.run(
        legal_approval_preflight_reason(cast(AsyncSession, session), version, source, attestation)
    )


def test_current_copy_preflight_accepts_text_layer_with_unknown_statutory_dates() -> None:
    session, version, source, attestation = _candidate()

    assert _reason(session, version, source, attestation) is None
    assert version.publication_date is None
    assert version.version_date is None
    assert version.approval_state == "REVIEW_REQUIRED"


@pytest.mark.parametrize(
    ("basis", "scope", "limitations", "artifact_kind"),
    [
        ("DATED_EDITION", "TEXT_LAYER", [], "THIRD_PARTY_VERIFIED_COPY"),
        (
            "LAWYER_CURRENT_COPY",
            "FULL_DOCUMENT",
            ["Graphics excluded."],
            "THIRD_PARTY_VERIFIED_COPY",
        ),
        ("LAWYER_CURRENT_COPY", "TEXT_LAYER", [], "THIRD_PARTY_VERIFIED_COPY"),
        ("LAWYER_CURRENT_COPY", "TEXT_LAYER", ["Graphics excluded."], "OFFICIAL_RAW"),
    ],
)
def test_preflight_rejects_invalid_current_copy_metadata(
    basis: str, scope: str, limitations: list[str], artifact_kind: str
) -> None:
    session, version, source, attestation = _candidate()
    version.date_basis = basis
    version.normalization_scope = scope
    version.extraction_limitations = limitations
    version.artifact_kind = artifact_kind
    if artifact_kind == "OFFICIAL_RAW":
        source.trust_level = "PRIMARY"
        attestation.source_is_official = True

    assert _reason(session, version, source, attestation) is not None


@pytest.mark.parametrize("field", ["current_copy_confirmed", "extraction_limits_understood"])
def test_current_copy_preflight_requires_copy_flags_even_when_dates_are_attested(
    field: str,
) -> None:
    session, version, source, attestation = _candidate()
    attestation.effective_dates_verified = True
    setattr(attestation, field, False)

    assert _reason(session, version, source, attestation) is not None


def test_dated_edition_preflight_keeps_full_document_copy_approval() -> None:
    session, version, source, attestation = _candidate()
    version.date_basis = "DATED_EDITION"
    version.normalization_scope = "FULL_DOCUMENT"
    version.extraction_limitations = []
    attestation.effective_dates_verified = True
    attestation.current_copy_confirmed = False
    attestation.extraction_limits_understood = False

    assert _reason(session, version, source, attestation) is None


def test_dated_edition_cannot_use_current_copy_flags_instead_of_verified_dates() -> None:
    session, version, source, attestation = _candidate()
    version.date_basis = "DATED_EDITION"
    version.normalization_scope = "FULL_DOCUMENT"
    version.extraction_limitations = []

    assert _reason(session, version, source, attestation) is not None


def test_current_copy_preserves_659_end_with_a_later_confirmed_applicability_floor() -> None:
    session, version, source, attestation = _candidate()
    session.document.official_number = "659"
    version.effective_to = date(2031, 9, 1)
    attestation.expected_effective_to = version.effective_to

    assert _reason(session, version, source, attestation) is None


def test_dated_edition_keeps_exact_659_boundaries_instead_of_a_copy_floor() -> None:
    session, version, source, attestation = _candidate()
    session.document.official_number = "659"
    version.date_basis = "DATED_EDITION"
    version.normalization_scope = "FULL_DOCUMENT"
    version.extraction_limitations = []
    version.effective_to = date(2031, 9, 1)
    attestation.expected_effective_to = version.effective_to
    attestation.effective_dates_verified = True
    attestation.current_copy_confirmed = False
    attestation.extraction_limits_understood = False

    assert _reason(session, version, source, attestation) == (
        "PAID_MEDICAL_SERVICES_BOUNDARY_MISMATCH"
    )


def test_current_copy_can_clip_736_start_without_extending_its_known_end() -> None:
    session, version, source, attestation = _candidate()
    session.document.official_number = "736"
    version.effective_from = date(2026, 8, 22)
    version.effective_to = date(2026, 9, 1)
    attestation.expected_effective_from = version.effective_from
    attestation.expected_effective_to = version.effective_to

    assert _reason(session, version, source, attestation) is None


@pytest.mark.parametrize(
    ("number", "effective_from", "effective_to"),
    [
        ("659", COPY_VALID_FROM, date(2031, 9, 2)),
        ("659", date(2026, 8, 31), date(2031, 9, 1)),
        ("736", COPY_VALID_FROM, date(2026, 9, 1)),
        ("736", COPY_VALID_FROM, None),
    ],
)
def test_current_copy_cannot_extend_known_boundaries_or_revive_expired_736(
    number: str, effective_from: date, effective_to: date | None
) -> None:
    session, version, source, attestation = _candidate()
    session.document.official_number = number
    version.effective_from = effective_from
    version.effective_to = effective_to
    attestation.expected_effective_from = effective_from
    attestation.expected_effective_to = effective_to

    assert _reason(session, version, source, attestation) is not None


@pytest.mark.parametrize(
    ("broken_field", "expected_field", "reason"),
    [
        ("raw_sha256", "expected_sha256", "STORED_RAW_SHA_MISMATCH"),
        ("normalized_sha256", "expected_normalized_sha256", "STORED_NORMALIZED_SHA_MISMATCH"),
        ("fragments_sha256", "expected_fragments_sha256", "STORED_FRAGMENTS_SHA_MISMATCH"),
    ],
)
def test_current_copy_preflight_keeps_stored_checksum_guards(
    broken_field: str, expected_field: str, reason: str
) -> None:
    session, version, source, attestation = _candidate()
    setattr(version, broken_field, "d" * 64)
    setattr(attestation, expected_field, "d" * 64)

    assert _reason(session, version, source, attestation) == reason


def test_current_copy_preflight_keeps_verified_source_trust_required() -> None:
    session, version, source, attestation = _candidate()
    source.trust_level = "PRIMARY"

    assert _reason(session, version, source, attestation) == "SOURCE_TRUST_LEVEL_MISMATCH"


def test_current_copy_preflight_keeps_complete_bound_fragments_required() -> None:
    session, version, source, attestation = _candidate()
    session.fragments.clear()

    assert _reason(session, version, source, attestation) == "NO_FRAGMENTS"
