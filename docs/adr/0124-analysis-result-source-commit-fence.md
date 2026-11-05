# Fence normal analysis results with their selected approved sources

Status: accepted, 2026-11-05.

Normal V1/V2 submissions validated their context after retrieval, then persisted
reports, verifier runs, risk assessments and success receipts without locking or
rechecking the selected legal and clinic sources at the transaction boundary.
A clinic retirement event or legal reviewer withdrawal during report persistence
could therefore leave a report accepted from sources already absent from the
approved production views.

Extract the existing lawyer-draft provenance guard into
`analysis_source_freshness.require_analysis_sources_current`. Normal V1/V2 result
branches call that guard after their final write flush and before the existing
reference-example/material checks, current actor/subscription recheck and final
job-lease check. It locks the exact
selected policy, legal versions, their source/document/reviewer metadata, and
clinic versions/documents until the caller commits or rolls back. It rechecks the
original selected IDs, order, approved view predicates, effective dates, content
and exact hashes after all actual lock waits. It never substitutes newly retrieved
evidence for the selection used to generate the claims.

The lawyer-draft entry point remains a compatibility wrapper preserving its
`LAWYER_DRAFT_CONTEXT_STALE` error and material fence. Normal results use the
existing `ANALYSIS_CONTEXT_STALE` 409 and roll back report, verifier, risk and
receipt writes together. No approval rule, trusted source, risk-policy version,
LLM integration or database schema changes.

Real PostgreSQL regressions cover V1 and V2 with retirement/withdrawal after
retrieval, actual row-lock waits followed by withdrawal, and blocking concurrent
legal/clinic/policy withdrawal until the report transaction commits. Existing
lawyer-source tests verify compatibility, including exact-hash and lease checks.
Membership revocation and subscription expiry during actual source lock waits also
roll back both report versions and the report-free V2 result, using the existing
access guard and database wall clock.

References:
- https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-ROWS
- https://www.postgresql.org/docs/current/transaction-iso.html#XACT-READ-COMMITTED
- https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#orm-queryguide-populate-existing
