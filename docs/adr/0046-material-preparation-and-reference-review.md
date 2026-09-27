# ADR-0046: Immutable material preparation and separate reference review

- **Status:** Accepted contract; implementation tracked in tasks P1–P11
- **Date:** 2026-09-27

## Context

The owner approved preparation of the entire supplied package and separate human
confirmation of normative versions and reference originals inside the existing
seven groups. Receipt rows are immutable provenance, not legal versions. Seven
clinical PDFs and one blank form must not become laws merely to enable a button.
Some originals bundle several code parts; a shared checksum is not proof that all
parts have been prepared. Consolidated-edition dates cannot be invented from the
date of adoption or receipt.

## Decision

Preserve incoming receipts. Store additive, immutable preparation revisions with
original identity/hash, a typed metadata payload, known-field provenance, declared
parts, extraction limitations and separately stored text. A canonical metadata
digest includes the normalized text hash. Unknown fields stay unknown. Preparation
does not set approval, source trust or retrieval eligibility.

Revisions are ordered under a material lock, and an identical preparation digest
reuses its original record without making it current again. DB constraints bind
the receipt ID/hash and the metadata/text digests. Insert guards and append-only
triggers protect history; runtime grants deny preparation update/delete.

Reference review is a separate append-only event on an exact preparation and
original hash. A group preview lists the exact selected references and caveats;
an explicit human attestation confirms review of these originals, not normative
effect or perfect text extraction. Actor/time and batch identity remain auditable.
Stable idempotency keys and request hashes distinguish replay from key misuse;
stale/concurrent changes reject atomically rather than partly confirming a group.
Only the existing active platform LEGAL_EDITOR/gateway-key boundary may confirm.

Normative preparation reuses the existing corpus loader and per-version approval
guards. Every intended document part has an explicit immutable preparation/version
association. The unified view must not hide an incomplete bundle after one part
has been prepared. Alternate copies retain their original downloads and provenance.

No clinical reference or blank form is admitted to legal retrieval by a review
event. Production legal retrieval still requires an applicable APPROVED version.
The assistant/importer does not perform the human confirmation.

## Alternatives considered

- Rewrite receipt rows: rejected because it changes receipt identity and breaks
  repeat imports and historical provenance.
- Put every PDF/RTF into LegalVersion with placeholder dates: rejected because
  schema satisfaction would misrepresent legal identity and applicability.
- Reuse normative approval for references: rejected because a reference review
  is not a legal approval and must not alter evidence eligibility.
- Add another top-level queue: rejected; the owner requested the same seven groups.

## Verification and rollout

Use synthetic unit/API/concurrency/rollback tests and disposable PostgreSQL for
migration upgrade/down checks. Verify the real private package separately without
committing its original contents or extracted text. Metadata listing must not read
artifact payloads. Missing evidence stays visible and prevents false all-ready claims.

Schema changes are additive; migration/deployment follows the normal reviewed GitHub
workflow. Roll back application code if needed while retaining immutable records.
Never downgrade/drop production review history. Release checks are read-only and
must not submit actual legal approvals or reference attestations.
