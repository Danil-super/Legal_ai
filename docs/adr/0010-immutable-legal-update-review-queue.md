# ADR-0010: Queue legal updates as immutable, hash-only review candidates

## Status

Accepted

## Date

2026-08-23

## Context

Legal publications can change after an artifact has been reviewed. The system needs to detect and
compare a newly parsed version without silently putting it into production retrieval. The queue
also must not copy legal text into operational/audit records unnecessarily, nor treat a clinic
administrator as an authority for a platform-wide legal source.

## Decision

Legal Core creates a global, append-only `legal_update_review_items` record only for a candidate
legal version whose source is already `APPROVED` and whose state is `REVIEW_REQUIRED`. The record
binds the source, document, previous/candidate version IDs, raw/normalized/fragment checksums and
a structural diff made of paths and text hashes. It has no `clinic_id`, because normative sources
are platform-wide rather than tenant-owned data.

The candidate digest is unique, so a retry returns the existing review item only when every bound
identity and checksum matches. PostgreSQL verifies candidate/source/document identity on insert
and prevents updates or deletion. The queue has no transition to `APPROVED`; only the existing
checksum-bound human legal-editor attestation and regression process may change a legal version.
The persistence service takes pre-fetched, locally parsed input and has no network-fetch code.

Every updater attempt also appends a global `legal_update_runs` ledger record keyed by an
idempotency digest. It records only a deterministic result hash and a fixed failure code, or the
linked review-item ID. Raw response bodies, parser exception text and other arbitrary diagnostics
are excluded, so operational failures remain auditable without becoming a secondary document or
PII store.

## Alternatives considered

### Auto-promote after a clean structural diff

Rejected because a technical diff cannot establish legal applicability, source completeness or
the adequacy of selected fragments for a recommendation scenario.

### Store the full prior and candidate legal text in the queue

Rejected because immutable artifact/version tables already retain the authoritative text. The
queue requires only review traceability, which is satisfied by IDs and hashes.

### Make each clinic maintain its own legal-update queue

Rejected because the corpus and its approval lifecycle are platform-wide. Tenant-scoping this
data would duplicate legal truth and could lead to incompatible source versions across clinics.

## Consequences

- Repeated updater work is idempotent and cannot overwrite an earlier review candidate.
- A draft or untrusted source is rejected both by the service and by the database trigger.
- Fetch, parse and validation failures can be traced by code and result hash without raw error
  text; their ledger records are also immutable.
- A future network fetcher must be separately allowlisted, preserve raw bytes and record its
  failures without changing the approval rules in this decision.
- This decision does not enable recommendations, LLM/Hermes, source approval or automatic
  patient communication.
