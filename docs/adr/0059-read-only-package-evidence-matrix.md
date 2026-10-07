# ADR-0059: Read-only gap matrix before scaling normative preparation

- **Status:** Proposed implementation for owner review
- **Date:** 2026-10-07

## Context

The 58 immutable originals have been delivered, while the offline P8 inspector
finds 54 intended normative parts and eight references. Title-bound URLs and
scoped hashes are candidate extraction results, not verified edition dates or
proof that tables and figures survived conversion. Existing preparations may
remain partial. Six earlier corpus versions have no proven relationship to the
new originals. The one-part P11 pilot does not safely import two multi-part code
bundles or resolve missing legal metadata.

## Decision

Before extending the importer, add a private operator inventory and deterministic
gap-matrix generator. Its exact input is versioned and bounds the operation to
58 originals, 54 expected part keys, eight references and six explicit legacy
version UUIDs. The database is read through a `REPEATABLE READ READ ONLY`
transaction, selecting only allowlisted receipt/preparation/binding/version
columns; raw bytes and normalized document text are never selected. The exact
binding material, original checksum, part-text checksum and bound-version
checksum are checked against the fixed inventory and current preparation.

The matrix exposes existing candidate public-law metadata and exact preparation
locators for human verification, plus explicit blockers. It does not infer
unknown fields, certify their provenance, compare old editions, or approve
anything. Old versions are a separate list containing only their existing
binding state. Output is an owned `0600` file in a private directory, created
with exclusive/no-follow flags; CLI stdout stays empty and errors contain no
candidate values. Input and output schema are documented in
`docs/legal-review/package-evidence-contract.v1.md`.

The output's SHA-256 protects against accidental local change, not malicious
rewriting or legal inaccuracy. No new table or trusted-source policy is needed.
The operator must still verify real originals, public-law metadata and
completeness before a separate multi-part importer can bind versions. Final
APPROVED status remains exclusively with the authenticated editor API.

## Alternatives rejected

- Auto-populate missing dates from act adoption or receipt time: a consolidated
  edition may have different publication and effective dates.
- Guess links to six prior versions by title, number or raw hash: none proves
  the same act, part and edition; two distinct laws can share a number.
- Include full text in the gap report: it expands leak scope without helping
  identify missing fields and violates the package's private-document boundary.
- Write matrix rows to production storage: the immutable preparation and review
  ledgers already hold the authoritative state; this is an operator snapshot.

## Verification and limits

Synthetic tests check fixed counts, unsafe input rejection, exact DB inventory,
partial preparation blockers, distinct legacy rows, checksum tampering, private
output mode and silent CLI behavior. PostgreSQL integration uses disposable
data and checks preparation counts before/after. No real documents enter tests.
The matrix alone cannot prove that all 54 parts are correctly identified or
that a locator describes the cited text. It must never be treated as a release
or legal-review attestation.
