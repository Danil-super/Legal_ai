# ADR-0007: Revise reviewed fragment selections without mutating the legal artifact

## Status

Accepted

## Date

2026-08-22

## Context

The official PDF and its raw SHA-256 must remain immutable. A human reviewer can nevertheless
find that a `REVIEW_REQUIRED` selection omitted a relevant, verbatim structural fragment. The
previous uniqueness rule on `(document_id, raw_sha256)` made that correction impossible without
overwriting the stored candidate or duplicating the legal document.

## Decision

An official raw artifact may have several append-only `REVIEW_REQUIRED` extraction revisions.
They share `document_id` and the immutable raw SHA-256 but have different fragment aggregate
hashes and monotonically increasing `version_no`. An identical manifest remains idempotent.

A `dental-legal-corpus.selection.v1` manifest references a sibling verified v2 `OFFICIAL_RAW`
manifest and supplies only the new structural fragment selection. Legal Core resolves the base
artifact and complete normalized text itself; the selection cannot point outside that directory
or to a normalized excerpt.

At most one extraction revision of a raw artifact can be `APPROVED`, enforced by a partial
unique database index. The approval service rejects a candidate superseded by a newer extraction
revision, so reviewers cannot accidentally approve an outdated, narrower selection.

## Consequences

- The raw PDF, its SHA-256 and the previously reviewed candidate remain auditable.
- A broader selection creates a fresh review candidate and requires a fresh human attestation.
- Production retrieval continues to read only `APPROVED` versions; no selection change enables
  recommendations by itself.
- This mechanism is for extraction/annotation revisions, not a substitute for a later legal
  amendment with different official raw bytes.
