# ADR-0005: Submit a Telegram case through one durable workflow

## Status

Accepted

## Date

2026-08-22

## Context

A Telegram conversation is process-local and may disappear on restart. Creating a case at the
start of the wizard leaves empty records after cancellation. Calling case creation, fact writes,
finalization and report creation as separate requests also exposes partial state: a lost response
can make a retry create another case or report.

The clinic must be resolved by Legal Core from the authenticated Telegram actor. Neither callback
data nor a request payload may choose `clinic_id`.

## Decision

The gateway performs only `GET /v1/actor` when the wizard starts. It generates a random workflow
UUID and embeds that UUID in the final Telegram callback. No case is stored before confirmation.

`POST /v1/telegram-case-workflows/{workflow_id}/submissions` validates the complete fact set and,
inside one PostgreSQL transaction, creates the case, immutable facts, blocked intake report and a
durable `telegram_case_workflows` row. A transaction-scoped advisory lock serializes concurrent
submissions for the same UUID. A repeated identical submission returns the original resources;
reuse with different facts is rejected.

The workflow row stores tenant and actor ownership, request SHA-256 and references to the case and
report. It does not duplicate the report or medical text. After a gateway restart,
`GET /v1/telegram-case-workflows/{workflow_id}` reconstructs the response from the tenant-scoped
case and report, so the original callback can download the same PDF.

## Alternatives considered

### Persist the Telegram conversation state

Rejected as the idempotency boundary. Persistent conversation state could improve draft UX, but
it does not make four independent Legal Core mutations atomic and would add another sensitive
copy of incomplete facts.

### Create a case when the user opens the wizard

Rejected because cancellation and timeout would accumulate empty cases.

### Keep four idempotency keys only in `context.user_data`

Rejected because process loss removes those keys and the association between the case and report.

## Consequences

- Cancellation before confirmation creates no case or fact rows.
- Lost responses and repeated callbacks return one case, one fact set and one report.
- Telegram callback data remains below the 64-byte platform limit.
- Workflow rows are tenant-scoped, RLS-protected and immutable.
- Legal analysis remains blocked; this decision does not enable recommendations or drafts.
