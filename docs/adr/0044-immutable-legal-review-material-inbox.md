# ADR-0044: Store attorney-supplied review materials outside the retrievable corpus

- **Status:** Accepted
- **Date:** 2026-09-25

## Context

The owner provided a lawyer-selected package containing legal copies and clinical PDF references.
The legal editor needs to inspect the exact supplied files in Telegram before preparing a complete
legal version. The package does not reliably contain all metadata required by `LegalVersion`: a
canonical document identity, issuer and number, effective dates, a complete normalised text and
selected checked fragments.

Putting the raw files directly into `legal_versions` would make incomplete material look like
corpus evidence and would blur the distinction between legal and clinical sources.

## Decision

Keep incoming files in a separate immutable `legal_review_materials` table. Each record preserves
the package key, source metadata known at receipt, bytes, SHA-256, MIME type and receipt time.
Only a platform `LEGAL_EDITOR` authenticated through the Telegram gateway can list metadata or
download the file. The table accepts only a `LEGAL_COPY` or `CLINICAL_REFERENCE` with
`METADATA_REQUIRED`; it has no approval state and is not queried by Legal Core retrieval.

The import command is idempotent by package key and raw checksum; retries retain the original
receipt timestamp. Files are supplied to an explicit import command (a protected temporary copy
or a read-only mount); the application does not automatically scan or promote files. Safe RTF
files without an identifiable source URL remain review materials with unknown provenance, not
trusted sources. Multiple source links likewise require an editor to resolve the canonical page.
A legal copy can become a production source only when an editor creates a
complete version and makes the existing explicit approval attestation. Clinical references remain
outside legal recommendations.

## Consequences

- Editors can view the supplied originals without Telegram exposing them to ordinary users.
- A supplied file cannot become `APPROVED`, legal evidence, or a patient recommendation by import.
- The database stores the package once; re-running a verified import does not duplicate it.
- The metadata-to-version editor workflow remains a distinct implementation step, rather than
  treating a download as legal approval.
