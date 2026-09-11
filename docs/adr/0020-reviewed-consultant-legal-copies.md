# ADR-0020: Admit authorised ConsultantPlus copies only as reviewed secondary evidence

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

The official publication portal is the preferred primary source, but it is not consistently
reachable from the deployment network. The product owner has supplied current PDFs downloaded
from ConsultantPlus and explicitly authorised that publisher as a source for the initial legal
corpus. Treating those bytes as `OFFICIAL_RAW` would make their provenance false even when the
underlying act is an official law.

The corpus must stay reproducible, keep the publisher visible to legal editors and preserve the
human approval boundary before any fragment reaches recommendations.

## Decision

Legal Core introduces manifest version `dental-legal-corpus.v3` and the artifact kind
`THIRD_PARTY_VERIFIED_COPY`. It is accepted only for a complete, checksum-locked PDF with full
normalised text, page count, retrieval time, exact selected fragments and the configured
`www.consultant.ru` allowlist. The matching immutable legal source is labelled
`VERIFIED_COPY`, never `PRIMARY`.

A `LEGAL_EDITOR` may approve this artifact only after affirming that the copy was compared with
the official text and required revision, as well as the existing checks for artifact completeness,
effective dates and selected fragments. The audit record stores that the source is not an official
publication and that the comparison was completed. The editor workspace describes the artifact as
a ConsultantPlus copy and does not label its link or downloaded PDF as an official source.

The database independently verifies the source trust level, immutable hashes, complete-document
metadata, effective dates, selected-fragment integrity and the matching approval-policy version.
`NORMALIZED_EXCERPT` remains unapprovable. Production retrieval remains restricted to approved,
date-applicable versions; ingestion itself never approves a document.

## Alternatives considered

### Relabel the supplied PDFs as `OFFICIAL_RAW`

Rejected because the bytes came from a commercial legal-information publisher rather than the
official publication portal. It would invalidate the provenance displayed to lawyers and in audit.

### Import only manually typed excerpts

Rejected because an excerpt cannot demonstrate completeness or allow a lawyer to verify the
current edition against the full supplied copy.

### Automatically approve a known commercial publisher

Rejected because publisher reputation does not prove a particular file's identity, full text,
applicable version or selected fragments.

## Consequences

- The five supplied PDFs can be queued for legal review without misrepresenting their origin.
- A legal editor must consciously compare each copy before it becomes available for retrieval.
- Adding a different commercial publisher requires a separate trusted-source decision and an
  explicit allowlist extension; a manifest cannot select arbitrary hosts.
- The official portal remains the preferred route for future `OFFICIAL_RAW` artifacts.
