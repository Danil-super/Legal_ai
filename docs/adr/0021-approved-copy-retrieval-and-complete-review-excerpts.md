# ADR-0021: Complete human review and retrieval of approved legal copies

- **Status:** Accepted
- **Date:** 2026-09-13

## Context

ADR-0020 permits reviewed ConsultantPlus copies, but the production SQL view still excluded
their artifact kind even after a valid human approval. The editor's PDF button also concealed
the selected excerpts that the same editor was required to attest. Telegram previews truncated
long excerpts, so they could not support a complete review.

## Decision

Align the production view with the already accepted v2 approval contract: both `OFFICIAL_RAW`
and `THIRD_PARTY_VERIFIED_COPY` may enter retrieval only through an `APPROVED` source/version
and a matching `legal_approval_event_is_current` event. Preserve that function's actor, trust,
attestation, checksum and effective-range guards. Retrieval still checks the requested date.
No ingestion, migration or export approves content. No trusted source or risk policy changes.

Keep **Open PDF** as the primary editor action. Add an authenticated
`GET /v1/legal/review-queue/{version_id}/excerpts` attachment with every selected excerpt in
ordinal order, full text, recorded article/part/point/path, version/source/date metadata,
artifact/selection hashes and per-excerpt hashes. It is UTF-8 plain text, not HTML or truncated
Telegram messages. Both gateway credential and an active `LEGAL_EDITOR` are required. The
server checks the selection and individual hashes, bounds the export to the same 50 MB cap
as PDF delivery, and the gateway verifies size and SHA256 before sending it. The export says
explicitly that PDF pages have not been mapped and that article/path labels must be compared
with the full PDF; it never invents page locations or claims the selection is the whole law.

Both attachments provide a return-to-card and return-to-list action. All four human attestations
remain explicit. Opening or downloading a document is not evidence of having reviewed it.
Numeric PDF size, page count and selected-fragment count are rendered correctly.

The review queue loads metadata only, without PDF bytes or full normalized text. It explicitly
returns `approvalPreflightChecked=false` and conservative `approvalEligible=false`; the UI says
the integrity/eligibility check happens when opening the card, not that the version is unavailable.
Full immutable preflight remains mandatory on the card and under the approval transaction lock.
Returning from attachments preserves attestations only for the same version/hash/date snapshot.

## Alternatives considered

- Auto-approve publisher copies: rejected; contrary to the human review boundary.
- Send all excerpts as Telegram messages: rejected; message limits truncate legal context.
- Publish PDF URLs publicly: unnecessary; editor-only attachment delivery already exists.
- Guess PDF page numbers from text order: rejected; the current parser stores no page map.

## Consequences

After lawyers approve a correct, applicable version, its selected fragments can support analysis.
This does not claim complete legal coverage: missing law, dates or relevant excerpts still block
unsupported claims. The migration downgrade restores the old view without deleting artifacts or
approvals. Reviewers can audit the entire selection rather than merely mark a hidden checklist.
