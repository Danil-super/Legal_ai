# ADR-0058: Transactional operator pilot for one evidenced legal part

- **Status:** Proposed implementation of the owner-approved P11 preparation work
- **Date:** 2026-10-07

## Context

P8 identifies candidate RTF headings and text scopes; P9 binds an immutable
preparation revision to an exact, unapproved corpus version. The existing corpus
loader commits its own transaction. Calling it before P9 could leave an unbound
version if association fails. The 50 incoming legal originals and 54 intended
parts currently lack verified edition metadata and full-text preparation. This
pilot does not supply those facts or claim that any real act is current.

## Decision and local contract

Add an operator-only CLI for **one** fully evidenced RTF part. There is no
Telegram/API route and no background importer. Its JSON manifest has schema
version `1`, exact `material_id`, `actor_user_id`, original `raw_sha256`,
`part_key`, scoped `part_text_sha256`, heading-bound `source_url`, a sibling
`corpus_manifest_path`, and a complete `MaterialPreparationInput` under
`preparation`. That preparation contains field-by-field evidence locators for
the canonical identity and dates, full normalized text and a completeness
locator. The sibling corpus manifest is the existing `dental-legal-corpus.v4`
schema and names an exact sibling RTF artifact. Paths must be package-local
regular files; inputs have size limits and are read without following symlinks.

The operator verifies P8 heading/source and one-part text boundaries against
the exact RTF, compares the preparation and corpus identities, dates, text
hashes and source, then uses the existing loader and P9 binder. A small loader
refactor permits its unchanged guarded ingestion logic to run in a caller-owned
transaction. Preparation revision, source/document/version/fragments and P9
association commit together or roll back together. Exact retries return the
existing immutable IDs while that preparation is current; an older manifest
cannot report success after a newer preparation revision. The active
`LEGAL_EDITOR` guard stays in P9; a revoked
editor makes the entire transaction fail. No status is changed to `APPROVED`.
The manifest's `actor_user_id` is privileged operator provenance, **not** proof
that this human reviewed the document or invoked the CLI. The final legal
attestation remains the separately authenticated editor approval API.

The CLI defaults to dry-run: it executes all database guards and rolls back.
Only an explicit `--commit` persists a `REVIEW_REQUIRED` candidate. Output and
errors disclose IDs/status only, never source text or raw medical/patient data.
For a disposable database, an operator can run:

```bash
python -m legal_core.single_part_operator /secure/pilot/operator.json
```

`--commit` is reserved for a separately reviewed import plan; deploying this
pilot is not authority to import or approve the 54 real parts. The supplied
evidence locators are candidate attestations, not proof that a cited edition,
date or text is legally correct. A human editor must verify them and separately
approve each normative version before retrieval can use it.

## Rejected alternatives and limits

- Sequentially call the old loader and binder: an association failure leaves an
  unbound version, contrary to the pilot's all-or-nothing contract.
- Embed synthetic or guessed dates to make a package pass: prohibited. Missing
  or conflicting evidence remains a blocker.
- Infer completion of a multi-part code bundle from one bound part: prohibited.
  This CLI rejects all multi-part preparations; later P11 work must process and
  account for every intended part.
- Treat an RTF heading URL or checksum as official publication/current-law
  verification: neither establishes edition currency or full extraction.

The pilot does not reconcile the six older differently hashed corpus versions,
prepare the real 50 legal originals, or assert that all seven groups are ready.
Those are separate gates under the grouped-review specification.

## Verification

Synthetic tests cover parser and path rejection, field evidence, tampered bytes,
no-raw CLI errors, dry-run rollback, exact idempotent replay, late authorization
rollback, unchanged loader behavior and the P9 append-only guards. Integration
tests run only against a disposable PostgreSQL database. No actual private
document is committed to the repository or used as a test fixture.
