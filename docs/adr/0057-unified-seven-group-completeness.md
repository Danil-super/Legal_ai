# ADR-0057: Determine seven-group completeness from every prepared part

- **Status:** Proposed implementation of the owner-approved grouped-review contract
- **Date:** 2026-10-07

## Context

The editor showed incoming originals and corpus versions as unrelated rows. Matching
raw checksums could hide an original even when no verified preparation-to-version
association existed. One RTF can also contain several independent code parts, so
matching one raw artifact is not proof that all its parts were prepared or approved.
Clinical references and form 043/у have a separate human review ledger and must
never be counted as approved law.

## Decision

Use only current preparation revisions and the immutable P9 part/version bindings
to collapse an exact bound original into its current corpus-version card. Keep the
original file downloadable from that card. A partly bound original stays visible
as a preparation card, while each bound part has its own version card. The current
preparation's declared part count, not a shared SHA-256, determines missing-part
progress. A newer preparation revision or an expired/superseded corpus version
reopens a visible gap; prior binding history is never deleted.

The seven-group API reports distinct counts for approved normative versions,
separately reviewed references, missing prepared parts and originals with unknown
part inventories. A group is complete only when it is nonempty, no source/part gap
or unlinked version remains, all displayed current normative versions are
`APPROVED`, and all reference originals have their own review event. This is
editor-workflow completeness, not
a legal coverage, current-law or case-answer guarantee. Production retrieval still
uses only effective, `APPROVED` versions.

Versions without a verified P9 binding remain separate and explicitly labelled as
possibly alternate copies with an unconfirmed source relationship. In particular,
the six older differently hashed versions must not be merged with the new originals
by title or act number. A future, fully evidenced canonical identity comparison or
an explicit reviewed association may link them; P11 must account for them before
claiming the supplied package is fully reconciled. The `63-ФЗ` collision across
different acts illustrates why number-only matching is unsafe.

## Alternatives considered

- Suppress any original with the same raw SHA-256 as a version: rejected because it
  hides unbound code parts and provides no immutable provenance.
- Match versions to originals by displayed title or official number: rejected
  because these are neither unique nor evidence of the same edition.
- Add a new mutable alternate-copy mapping: rejected for this slice because no
  approved identity/edition contract or migration exists for it.

## Verification and rollout

Synthetic PostgreSQL tests cover partial/full two-part binding, checksum-only
non-suppression, reference review progress and original download. Pure tests cover
mixed legal/reference completeness and unknown parts; Telegram tests cover source
links and readable part status. The API additions are additive. No new table,
approval event or trust-state transition is introduced. The release must apply P9
first, run the full quality suite, then verify all 58 originals and every intended
part read-only before any human group approval.
