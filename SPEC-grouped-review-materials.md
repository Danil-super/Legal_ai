# Grouped attorney-supplied review materials

## Scope approved by owner, 2026-09-25

Make the complete supplied package accessible to LEGAL_EDITOR in Telegram, grouped
by subject. Preserve original bytes and approval boundaries. Never imply that
receipt or a document count establishes legal approval or completeness of coverage.

## Increment 1 acceptance

- Import all 58 supplied files idempotently using the repaired importer.
- Show subject groups, exact group counts, ten-item pagination and back navigation.
- Keep the seven clinical references separate from legal copies.
- Every material remains downloadable by an active LEGAL_EDITOR only.
- Listing metadata must not read the raw artifact payloads from PostgreSQL.
- Show a route to the existing approval card only when this exact artifact already
  has a prepared legal version. Otherwise explicitly show metadata preparation is
  outstanding; do not offer a non-functional approval button.

## Follow-on gate (not satisfied by increment 1)

Preparing all legal versions and selected/batch human approval requires verified
canonical identity, source, version/effective dates, complete extracted text and
source-linked fragments. Ambiguous metadata remains for explicit editor input.
Do not create invented dates or arbitrary source URLs merely to satisfy a schema.

## Contract and implementation

Extend GET /v1/legal/review-materials with optional `group` (fixed enum), response
`groups` counts, `selectedGroup`, per-item `groupKey` and optional `versionId`.
Grouping is presentation-only; no migration, trust policy or approval change.
Use one shared deterministic SQL grouping expression for counts and filtering.
Telegram groups use bounded fixed callback identifiers, no filenames or raw text.

Implementation order: API/types and regression tests → Telegram grouping and
navigation tests → full quality gates → GitHub deployment → idempotent import →
verify all pages and all artifact checksums through authenticated API reads.

## Verification and boundaries

Commands: `.venv/bin/python -m pytest`, `.venv/bin/python -m ruff check .`,
`.venv/bin/python -m mypy services/legal_core/src services/gateway/telegram/src`;
Compose config with non-secret dummy required values. PostgreSQL tests use a
disposable database. Only synthetic fixtures; no original files enter git.
Original supplied files and unrelated worktree files are preserved. No source,
legal version, personal bot, risk policy or tenant permission is approved/changed.

## Increment 2: human group approval — owner-approved scope

### Objective and assumptions

The owner requested one human confirmation per subject group after a lawyer has
reviewed its documents. A group action is a convenience wrapper around explicit
per-version legal approval, not permission for automatic approval by a loader,
assistant or model. Existing LEGAL_EDITOR authorization remains unchanged.

Interaction:

1. Both "Проверка норм" and "Загруженные материалы" open only seven subject groups
   plus back navigation. Include the six prepared documents in these groups, not a
   separate initial queue. Inside a group show its members, original downloads,
   preparation status and links to full prepared versions and their fragments.
2. Open a batch preview showing the exact versions being confirmed, dates and
   count, with a separate list of blocked materials and reasons.
3. Present one explicit human declaration covering source/comparison, completeness,
   effective dates and extracted fragments for every listed version. The final
   button says "Подтверждаю проверку — утвердить N документов"; no per-file checkbox
   sequence is required and no declaration is pre-accepted.
4. Commit the shown batch atomically; return the exact approved count and explicitly
   state that unprepared materials remain unapproved.
   Preserve individual approval events plus a shared batch identity and actor/time.

Do not call a group complete if only a subset was approved. Existing approved
versions are reported separately, without duplicate approval events. A newly
uploaded file or changed extraction is never covered by an earlier confirmation.

### Approved decision — mixed readiness

Offer the explicitly listed ready subset; blocked materials remain visible and
unapproved. The button names the subset count, not "approve all". The owner approved
this behavior and placing the existing six prepared documents inside the groups.

Changes to the previewed membership, artifact hashes,
normalized text, fragments, effective dates or eligibility invalidate the preview
and require a fresh human confirmation. There is no silent best-effort approval.

### Preparation and clinical-reference boundaries

The currently imported package contains 51 RTF legal copies and seven PDF clinical
references. Import alone has created no LegalVersion for these incoming materials.
Batch approval must not be advertised as usable for them until metadata and
extraction preparation is implemented and verified against the actual package.
Missing source identity or effective dates require explicit editor input; do not
invent dates or choose an arbitrary cross-reference URL from a document.

The clinical group remains view/download-only in this increment; a distinctly
labelled human review acknowledgment requires a separate implementation. It cannot
receive normative APPROVED status. Reviewing these PDFs does not authorize clinical advice,
new source trust or their admission to production legal retrieval. Any expansion
of that evidence boundary requires a separately agreed contract.

### Structure and code style

- Legal Core: existing `legal_approval.py` is the per-version approval authority;
  batch orchestration must reuse it inside one caller-owned transaction.
- Telegram: `legal_library_runtime.py` owns bounded group navigation and explicit
  preview/confirm actions, with back/cancel available before submission.
- Tests: Legal Core contract/PostgreSQL tests and Telegram runtime tests in their
  existing service test directories; synthetic fixtures only.
- Document the critical approval contract in a new numbered `docs/adr/` record;
  any new persistent batch structure requires an Alembic migration.

Follow the existing typed, fail-closed style rather than direct status updates:

```python
await approve_legal_version_in_session(
    session,
    attestation,
    require_review_required=True,
    record_rejected_attempt=False,
)
```

### Verification and success criteria

Use the existing full commands:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy services/legal_core/src services/gateway/telegram/src
docker compose config --quiet
```

Integration tests must use a disposable PostgreSQL database, never production.
Require tests for unauthorized access, stale previews, concurrently changed group
membership/versions, concurrent reviewers, duplicate submission, transaction
rollback on any selected version failure, incomplete metadata, unsupported
clinical-reference approval, per-version audit and unchanged retrieval boundaries.
Runtime verification must not approve actual laws on behalf of a human lawyer.

Always preserve immutable originals, validate inputs, enforce the existing role,
and retain audit without raw document text. Ask before changing trusted sources,
permissions or evidence policy. Never auto-approve, fabricate provenance/dates,
mask partial results or turn a clinical review acknowledgment into legal approval.

Implementation: additive grouped-list and batch-preview/confirm contracts, existing
per-version approval guards and immutable ledger; see ADR-0045. No schema migration
or preparation of unresolved source metadata is included. Deployment status belongs
in tasks/todo.md, not in this specification.

## Increment 3: prepare every material and separate reference review

### Status, objective and assumptions

The owner explicitly approved separate confirmation of normative versions and
reference materials within the same seven groups (2026-09-27). This detailed
increment and the need for additive preparation/reference-review storage were
**approved by the owner on 2026-09-27**. This is permission to design and implement,
not a statement that a migration has run or the feature is deployed.

Module: existing `legal-corpus`, consumed by existing `telegram-gateway`; no new
capability or expansion of retrieval/medical-advice permissions. Prepare all 58
supplied originals plus their relationship to the six existing prepared versions.
The technical audit is `docs/legal-review/package-preparation-2026-09-27.md`.

Assumptions: all originals remain available; the lawyer reviews groups without a
per-file confirmation sequence; absent metadata is shown as unknown, not invented.
Clinical PDF review and blank-form review acknowledge the identified original;
they do not certify legal effect, exact edition dates or full text extraction.

### User-visible acceptance contract

1. Keep exactly seven landing-page groups. Inside a group show understandable
   titles, original downloads, preparation progress and the unresolved fields.
   Do not add another top-level queue or hide incomplete materials.
2. Where applicable, show two distinct actions: approve the explicitly listed
   ready normative versions; confirm review of the explicitly listed reference
   originals. The clinical group has only the second action. Form 043/у stays in
   healthcare as a reference, not a fabricated normative version.
3. Both actions display the exact count and members, require an explicit human
   attestation, preserve back/cancel and bind confirmation to the displayed
   immutable snapshot. New files, changed metadata or changed text require a new
   preview. Retries are idempotent; stale previews fail without partial approval.
4. Record reference review separately with actor/time, original SHA-256 and
   preparation identity. It must never set `LegalVersion.APPROVED`, approve a
   source or make a clinical text/blank form retrievable as legal evidence.
5. Prepare normative source identity, dates, original/normalised hashes, full
   scoped text and source-linked fragments using existing corpus guards. Unknown
   required dates remain blocked pending evidenced metadata or editor input.
   Successful extraction alone never means ready or approved.
6. Associate every source artifact with all intended canonical documents/parts.
   GК/NК bundle preparation is complete only when every intended part is accounted
   for; the shared raw checksum must not hide unprepared parts. Different copies
   of the same act retain provenance without misleading duplicate act cards.
7. A group is complete only when all its intended normative versions have explicit
   human approval and all references have separate human review, with no hidden
   preparation blockers. Report partial completion accurately.

### Structure, persistence and style

Preserve `review_materials.py` receipt semantics and original immutable rows.
Use additive, versioned preparation/reference-review persistence, subject to schema
approval and Alembic migration; exact tables/API schema belong in the reviewed
technical plan and a numbered ADR. Reuse corpus_loader/legal_approval guards for
norms and the current editor role, gateway authentication and group navigation.
No production LibreOffice/OCR dependency is proposed: conversion is offline;
server ingestion validates bounded, hash-bound prepared inputs.

Source: `services/legal_core/src/legal_core/`; Telegram rendering:
`services/gateway/telegram/src/`; tests remain in each service's existing tests.
Keep strict types, explicit immutable inputs and fail-closed errors, for example:

```python
if preparation.raw_sha256 != material.raw_sha256:
    raise ValueError("preparation does not match the original artifact")
```

### Verification and boundaries

Use the full commands above, disposable PostgreSQL migration/integration tests,
and synthetic fixtures only. Add regressions for heading-vs-cross-reference URLs,
RTF unsafe content, missing/contradictory dates, repeated act numbers in different
years, multi-part artifacts, partial extraction, unchanged receipt retries,
reference-vs-normative confirmation, role negatives, stale snapshots, concurrent
reviewers, replay, rollback and exclusion of reference material from retrieval.

Verify the actual private package separately: all 58 originals accounted for,
checksums preserved, all pages/items reachable, no incidental legal approval.
Never claim all seven groups ready while even one required normative field or
part is unresolved. Preparing reference cards does not imply full PDF OCR.

- Always: preserve originals, show exact scope and blockers, retain audit without
  raw text/PII, keep human approval and existing retrieval/date gates.
- Ask first: additive DB schema/migrations, new dependencies/CI changes, new trusted
  sources, changes to permissions or evidence policy; deployment after checks.
- Never: auto-approve, invent edition/effective/publication dates, overwrite receipt
  records, hide incomplete code parts, enable medical prescriptions, commit raw
  supplied documents or unrelated local files.

Review decision: the owner accepted this preparation/confirmation contract and
additive storage. Detailed implementation plan/tasks follow their own review;
approval of the specification is not a production mutation or legal approval.
