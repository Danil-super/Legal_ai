# ADR-0055: Bind prepared normative parts to exact corpus versions

- **Status:** Proposed implementation of the owner-approved grouped-review contract
- **Date:** 2026-10-07

## Context

The incoming material is an immutable receipt, not evidence. A single supplied
RTF may contain several code parts sharing one raw SHA-256. The existing corpus
loader creates guarded, non-approved versions, but checksum-only matching can
mistakenly make one prepared part look like a complete bundle. The editor also
needs to see which original and preparation revision each version came from.

## Decision

Add an append-only `legal_prepared_part_versions` edge from a specific preparation
revision and part key to one LegalVersion. Record the original ID/hash, scoped
text hash, active LEGAL_EDITOR actor and timestamp. The database guard verifies
the exact original raw bytes, a complete and current normative preparation,
scoped text, canonical document identity, evidenced dates, a Garant source and
fragments. It rejects update/delete even for the owner; the runtime role receives
only SELECT/INSERT after post-migration role provisioning. These records are global
legal-corpus provenance, not tenant-owned clinic data.

The offline P8 inspector determines the intended code-part boundaries and heading
source. P9 recomputes those candidates when binding and requires an evidenced
preparation revision with contiguous text scopes. Each part has its own immutable
edge and version. Missing dates, incomplete extraction, a stale preparation,
unknown source or mismatch leave the part unbound. No association operation sets
source status, `LegalVersion.APPROVED`, or production retrieval eligibility.

The existing `corpus_loader.ingest_manifest` remains the only version importer.
It commits before this association is added in a separate caller-owned
transaction. If binding fails, the link rolls back while an unbound
`REVIEW_REQUIRED` version may remain. That version is not legal evidence and must
remain an explicit blocker in P10 group completeness and P11 package reports.
Before importing, an operator should preflight the exact original, all intended
parts, dates, text scopes and manifest; on retry, use the same immutable manifest
and then bind each part. Never delete the orphan automatically or approve it as
a smoke test. A different edition needs a new preparation/version and human review.

## Alternatives considered

- Attach `version_id` directly to the receipt: rejected because one RTF can hold
  several independent canonical acts and preparation revisions are immutable.
- Treat equal raw checksums as full coverage: rejected because that hides missing
  code parts and mismatched scoped text.
- Refactor the corpus loader into a caller-owned transaction immediately: deferred
  to avoid changing the established loader/approval path in this slice. The safe
  orphan state is explicit and testable; P11 can add a combined transaction later
  if operational evidence makes it necessary.

## Verification and rollout

Use synthetic-only unit and disposable PostgreSQL tests for exact bytes, multiple
parts, stale revisions, actor access, rollback, retries, append-only triggers and
exclusion from `production_legal_fragments`. Apply the additive Alembic migration,
then rerun runtime-role provisioning before application traffic. In production,
roll back code rather than downgrade a migration that would drop audit history.
P10 must count latest-revision part keys and report unbound parts explicitly;
P11 must verify the private package separately without storing its raw text in
fixtures or logs. Human legal approval remains a later, separate event.
