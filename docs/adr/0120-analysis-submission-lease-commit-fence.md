# Recheck the analysis job lease at the transaction boundary

Status: accepted, 2026-11-05.

Normal V1/V2 submissions previously checked the lease only when acquiring the Job
row lock. A report, receipt flush or source check could wait past lease_until while
that lock remained held. The transaction then committed an expired worker result.
A row lock prevents another worker from taking ownership; it does not extend time.

Keep the existing Case -> Job lock order and initial actor, token, state and result
version checks. Flush all report/risk/verifier/receipt writes, complete the existing
source checks, then recheck the same locked job immediately before commit. All
normal result branches (including report-free V2) and precontext terminal results
perform this final check. A failure raises the existing 409
ANALYSIS_JOB_LEASE_EXPIRED and the request transaction rolls back together.

Lease checks read PostgreSQL clock_timestamp() after acquiring the job lock. This
is the actual current time, unlike now()/transaction_timestamp(), which stay at
transaction start even during lock waits. No lease extension, heartbeat or new
worker owner is implied. Direct submissions without job headers keep their existing
behavior. Completed responses still replay under a live lease; the initial current
lease check remains mandatory before replay.

Regression coverage uses real PostgreSQL/runtime-role requests for both result
versions: expiry during report persistence, an actual receipt INSERT trigger wait,
and a final source-check database wait. It verifies full rollback of reports,
receipts, risk and verifier records, and preserves valid submissions/replays.

References:
- https://www.postgresql.org/docs/current/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT
- https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.GenerativeSelect.with_for_update
