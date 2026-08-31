# ADR-0012: Keep unfinished Telegram intake as tenant-scoped, expiring drafts

## Status

Accepted

## Date

2026-08-31

## Context

Telegram conversation state is process-local. A clinic administrator needs to leave an unfinished
incident card, work on another card or restart the bot without losing the already entered
pseudonymous facts. Creating a `case` at the beginning of that flow would leave empty legal
records and could create duplicate reports.

The product owner approved storage of unfinished and archived draft cards with an automatic,
irreversible 30-day purge. This is a retention decision for pseudonymous intake data only; it does
not authorise new patient data, document uploads, legal recommendations or external processing.

## Decision

Legal Core owns a new `telegram_intake_drafts` record. Each row is tied to the resolved clinic and
the exact `clinic_users` membership, protected by PostgreSQL RLS, and additionally queried by
membership. The API never accepts a clinic or membership identifier from Telegram.

The gateway creates a draft before its first question and saves the complete, bounded current
wizard snapshot with the next state after each accepted answer. A server revision protects against
silently overwriting a concurrent session. The main menu lists only safe generated metadata
(category, next step and timestamp); it never exposes the free-text incident summary in the list.

The draft UUID is also the already-established idempotency UUID for final Telegram workflow
submission. No `cases`, facts, reports or recommendations are created until that final submission
succeeds. It then archives the draft; its separate 30-day retention period applies to `DRAFT` and
`ARCHIVED` rows. A Legal Core background task calls a static, schema-qualified database function
at startup and hourly. The function deletes only expired rows and has no input or access to draft
contents in logs or audit metadata.

## Alternatives considered

### Telegram `user_data` as the sole draft store

Rejected because it is lost on gateway restart and cannot safely support multiple cards or
authorisation after a user returns.

### Create the case before the card is complete

Rejected because it creates empty legal records and makes abandoned cards look like real cases.

### Retain drafts indefinitely

Rejected by the product retention decision; incomplete incident data should have a bounded
lifetime.

## Consequences

- Administrators can resume and switch up to 20 active cards belonging to their own clinic
  membership.
- A different administrator cannot retrieve or modify a draft by UUID.
- Leaving the wizard, menu navigation and timeout preserve the server draft; `/cancel` is a
  save-and-exit action.
- Retention failure is logged without draft data and retried later; it does not make Legal Core
  unavailable.
- The final workflow remains idempotent and is the only route that creates a confirmed case.
