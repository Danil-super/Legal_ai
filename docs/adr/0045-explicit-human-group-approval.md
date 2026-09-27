# ADR-0045: Unified editor groups and explicit human batch approval

- **Status:** Accepted
- **Date:** 2026-09-27

## Context

The owner requested seven intuitive groups instead of a separate six-document queue
and a mixed inbox. Lawyers should review a listed set and confirm it once. Incoming
materials still lack the metadata and extraction required for normative approval.

## Decision

The editor landing page lists exactly seven groups. A metadata-only union combines
incoming originals with the latest unexpired legal version of each document.
An incoming legal copy represented by the exact raw checksum of a visible version
is shown once, as that version. Clinical references remain separate. Grouping grants
no trust, approval or retrieval eligibility; approved versions remain visible.

Add these platform-LEGAL_EDITOR/gateway-key protected endpoints:

- `GET /v1/legal/editor/groups`: seven counts, no individual items.
- `GET /v1/legal/editor/groups/{group}?page=1`: ten items, metadata only.
- `GET /v1/legal/editor/groups/{group}/approval-preview`: exact ready versions/dates,
  blocked materials/reasons, already-approved count and content-bound snapshot.
- `POST /v1/legal/editor/groups/{group}/approval-events`: snapshot, exact ready ID
  set, four explicit human declarations and UUID `Idempotency-Key` header.

The group preview uses the existing integrity/eligibility guards and caps membership
at 200. Telegram paginates candidates and offers the final confirmation on the last
page. This is a human attestation, not proof that the person read every file. No
checkbox sequence per file is needed. Blocked materials are excluded explicitly,
and an empty ready set has no confirmation button. Clinical PDFs cannot be approved
as legal evidence through this action.

Confirmation locks selected versions and their sources in stable order, rechecks
membership/immutable fingerprints and exact candidate IDs, then calls the canonical
per-version approval function in one transaction. Stale previews return 409. A file
added after the confirmation's membership read is not part of the fixed selected
ID set and is never implicitly approved. Each version retains its individual human
actor, timestamp, hashes, regression checks and immutable event, with `batchId` in
the event checks. No raw document text is recorded in audit.

Use the existing unique actor/idempotency-key ledger, not a new batch table. The
first sorted version holds the root batch UUID; remaining event keys are UUIDv5
derived from that UUID and version ID. All events share the request digest and
commit atomically. A root-key retry returns the same count/IDs; reuse for a different
request returns 422. The existing advisory-lock namespace also protects against
single-approval key reuse. A gateway timeout retains the pending key for safe retry.

## Consequences

- No migration, new source trust, role, risk policy or retrieval policy is introduced.
- Existing single-version and inbox endpoints remain compatible with old messages.
- The prepared six versions are navigable in groups; the 58 originals are not
  automatically transformed into approvable versions. Preparation is separate work.
- List requests never fetch raw bytes; explicit preview/approval performs bounded
  per-version integrity checks. A larger corpus needs a separately designed batch
  selection protocol rather than silently lifting the 200-item bound.
- Disposable-PostgreSQL tests cover authorization, stale membership, rollback,
  concurrent reviewers, retry and clinical exclusion. Telegram tests cover root
  navigation, explicit declaration and stable retry intent.

## Rollout and rollback

Run unit/integration, lint, type, security and Compose gates before GitHub deployment.
Read-only production smoke checks groups, membership, artifact delivery, preview and
health; it must never submit an actual legal approval. Monitor existing API errors,
container health/restarts and list/preview latency. On failures, revert the feature
through the normal deployment workflow. Existing approval events remain valid and
must not be deleted or rolled back; old code can read them. No database rollback is
needed. The previous deployed revision is the rollback baseline recorded by deploy.
