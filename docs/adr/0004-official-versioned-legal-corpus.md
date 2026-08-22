# ADR-0004: Admit law through official, versioned and approved corpus records

## Status

Accepted

## Date

2026-08-22

## Context

Internet search and model memory cannot prove which text was applied, whether it was official or
whether it was in force on the incident date. Some relevant rules are published before their
effective date, and old/new rules may coexist during a transition.

## Decision

Legal Core stores immutable source artifacts and versions with SHA-256, official source URL,
retrieval time, version date and half-open applicability interval `[effective_from,
effective_to)`. Production retrieval returns fragments only from manually `APPROVED` versions
that contain the requested date.

Ingestion creates `REVIEW_REQUIRED` versions by default. Approval is a separate, audited action;
the downloader cannot approve its own result. The first mandatory regression boundary is the
replacement of Government Decree No. 736 by No. 659 on 2026-09-01.

An embedded or manually normalized excerpt is labelled `NORMALIZED_EXCERPT`. Database
constraints and the approval service prohibit approving it. A human legal editor may approve
only an `OFFICIAL_RAW` artifact loaded from an in-repository file, with matching SHA-256,
allowlisted official host, verified applicability dates, complete normalized text and fragment
checks. Every successful or blocked review attempt is append-only audit data.

## Alternatives considered

### Live web search for each case

Rejected because results are mutable, not reproducible and may not reflect the applicable date.

### Store only current consolidated text

Rejected because historical incidents require the version that applied at that time.

### Automatically approve an artifact after successful download

Rejected because transport success does not prove the document identity, completeness, parsing
quality or legal applicability.

## Consequences

- Initial ingestion includes a review/approval step before retrieval.
- Reports can cite a concrete fragment and immutable source checksum.
- Future and expired versions are excluded by query, not by prompt wording.
- Corpus maintenance requires legal review and regression tests at every effective-date boundary.
