# ADR-0019: Give only platform legal editors a bounded human-review workspace

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

The ordinary Telegram legal library correctly exposes only `APPROVED` legal versions. At present,
the initial official artifacts are `REVIEW_REQUIRED`, so a clinic lawyer cannot see enough
information to determine whether they are complete, authentic and applicable. The existing
checksum-bound approval service is safe but usable only through an operational CLI.

Clinic-lawyer case handling and platform-wide legal-corpus approval have different authority,
data and failure modes. Treating either a subscription, clinic role or platform-owner Telegram ID
as a substitute for a qualified legal editor would allow the wrong person to change production
legal evidence.

## Decision

Legal Core will expose a bounded global review workspace only after resolving an active
`LEGAL_EDITOR` system role server-side and verifying a separate gateway-to-Core secret. The
Telegram gateway injects `X-Legal-Editor-Gateway-Key` from `LEGAL_EDITOR_GATEWAY_KEY`; a supplied
Telegram ID alone is not authentication. The key is dedicated to this boundary and absent/invalid
configuration fails closed.

The workspace lists and details immutable candidates, serves their already-stored public artifact,
exposes bounded selected fragments and accepts four explicit human attestations. Approval remains
checksum- and effective-date-bound, idempotent and audited through the existing approval service.
An Alembic migration adds an editor-scoped idempotency key and request digest to the immutable
approval event. An advisory lock plus the legal-version lock serializes a replay and the resulting
approval in one transaction. Stale, legacy, already-approved and technically invalid requests are
rejected before they can create a false `BLOCKED` decision.

The workspace never fetches a reviewer-supplied URL, receives an upload, alters trusted sources,
changes risk policy, sees tenant-owned data or calls Hermes. `NORMALIZED_EXCERPT` legacy versions
remain visible for traceability but cannot be approved. A `BLOCKED` UI action is deferred until a
separate lifecycle transition is modelled and guarded in PostgreSQL.

## Consequences

- The normal **Нормативная база** remains a safe approved-only library.
- A platform editor can complete the human review without privileged server-shell access.
- A clinic lawyer cannot accidentally approve a source while processing an escalation.
- A legal version still becomes usable only after a qualified person deliberately confirms all
  attestations and Legal Core validates the immutable artifact.
- Deploying this workspace requires a new local production secret in
  `/etc/dental-legal-ai/app.env`; the secret is not committed and an omitted secret merely hides
  the editor control.
- This decision does not authorize the approval itself, activate legal recommendations or Hermes,
  or permit any automated patient response.
