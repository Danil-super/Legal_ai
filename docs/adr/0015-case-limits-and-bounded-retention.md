# ADR-0015: Enforce case capacity and purge confirmed case content on a fixed schedule

## Status

Accepted

## Date

2026-09-04

## Context

The clinic administrator interface must stay focused on a small, manageable number of live
incidents. At the same time, a confirmed incident can contain pseudonymous medical-service facts,
the internal report, a PDF and escalation discussion. Keeping that content indefinitely conflicts
with the approved pilot storage boundary.

The product owner approved the following operating policy:

- at most 5 active cases for one clinic owner or administrator;
- at most 30 newly confirmed cases for one clinic in a UTC calendar month;
- Telegram drafts do not count toward either case limit;
- confirmed case content and escalation discussion are kept for 90 days from confirmation;
- audit metadata, including the fact that a purge occurred, is kept for 12 months;
- clinic-document version history is outside this case-content purge and is not physically removed
  automatically.

## Decision

Legal Core is the only enforcement point. It serializes each clinic's lifecycle transition with a
transaction-scoped PostgreSQL advisory lock, so an owner cannot bypass the common clinic monthly
limit by using a different role or a concurrent request.

Creating an unfinished direct case checks only the five-case personal active limit. Confirmation
checks the shared monthly limit. A final Telegram submission checks both. `CLINIC_LAWYER` is not
permitted to create or change intake; the existing critical-escalation queue and its isolated
discussion remain the lawyer workflow.

At confirmation Legal Core records `closed_at` and a server-calculated `retention_due_at` exactly
90 days later. A background task calls a static schema-qualified, security-definer database
function at startup and hourly. For due cases, it deletes facts, reports and PDFs, durable Telegram
workflow data, idempotency responses, analysis records and escalation discussion. It then clears
case fields that could reveal incident context and marks the case `CONTENT_PURGED`.

The function leaves only a metadata-only `case_retention_events` row (tenant ID, case ID/number,
timestamp and deletion counts). This row and normal audit events are deleted after 12 months.
Neither raw case content nor free-text discussion is copied to audit metadata or process logs.
The metadata-only `CONTENT_PURGED` case tombstone follows the same 12-month expiry and is then
deleted as well.

The procedure is database-owned because the case evidence tables are normally append-only. The
function temporarily disables only the named immutable triggers inside its transaction, performs
the narrowly scoped due-case purge, and re-enables them before returning. Application endpoints
cannot use that bypass.

Report and analysis endpoints reject a case until the intake is confirmed. This prevents a report
from being created outside the retention lifecycle.

## Consequences

- An administrator can save any number of incomplete Telegram drafts subject to their separate
  20-draft / 30-day policy; drafts do not consume confirmed-case capacity.
- A confirmed case no longer consumes the active-case limit, but it consumes one monthly clinic
  slot even if its content is later purged.
- Once content is purged, all case-content endpoints return `410 CASE_CONTENT_PURGED`; no empty
  report or incomplete intake is reconstructed.
- A retention-job failure is logged without case content and retried on the next scheduled run.
- This decision does not authorise real patient data, Hermes/LLM processing, legal-corpus approval
  or automatic messages to patients.
