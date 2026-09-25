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
