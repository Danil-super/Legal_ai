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
