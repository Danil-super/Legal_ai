# ADR-0053: Freeze case facts at intake confirmation

- **Status:** Accepted
- **Date:** 2026-10-07

## Context

Legal Core creates reports, risk assessments and legal analyses from case fact
snapshots. The facts endpoint still accepted a new revision after intake
confirmation, changed the case status back to `COLLECTING`, and allowed a second
confirmation with a different idempotency key. A previously issued report could
therefore describe different facts from the current case.

## Decision

The first successful intake confirmation closes the fact collection for that
case. New fact batches and new confirmation requests return HTTP 409 with
`CASE_ALREADY_FINALIZED`. A retry using the original idempotency key still
returns its saved response. Both mutations lock the case row before checking
whether intake is closed, so concurrent requests cannot pass the check using an
outdated case state.

Corrections after confirmation require a new case and a fresh analysis. Existing
reports and analyses stay attached to their original fact snapshot. This rule
applies to the direct case API; the atomic Telegram workflow already creates a
complete, confirmed case in one transaction.

## Consequences

The API can no longer silently reopen a finalized case or make an issued report
inconsistent with its case facts. The user interface should direct a clinic
administrator to create a new case when facts change after confirmation. A
future explicit amendment workflow would need a separately versioned contract
and analysis invalidation rules.
