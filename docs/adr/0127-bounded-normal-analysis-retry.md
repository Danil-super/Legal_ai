# Retry one transient normal-analysis failure on the same durable job

Status: accepted, 2026-11-05.

A first provider wall timeout previously made an ordinary analysis job terminal
FAILED even when no result had been stored. A successful isolated long-running
request does not repair that recovery gap.

Under the existing locked Job row, `finish_job` first reads and validates the
retained result. Any valid normal receipt remains SUCCEEDED despite uncertain
HTTP completion, and retained HUMAN_REVIEW optional-draft handling stays unchanged.
Only an absent result with ANALYSIS_PROVIDER_UNAVAILABLE or
ANALYSIS_SERVICE_UNAVAILABLE and attempts < 2 returns to QUEUED. The same job ID,
actor, tenant, result version and idempotency scope stay bound. Attempts never
reset; error and lease fields clear together. The next normal claim increments
the counter and generates a new token. A second normal failure is terminal, and
old owners cannot finish or publish against the rotated/cleared token.

The existing PostgreSQL crash-recovery function and CHECK retain their three-claim
budget. The shared attempts counter counts every claim, including crashes; a
previous crash can conservatively consume the one normal retry. No extra attempts,
queue table, migration, lease extension or automatic retry of a retained optional
lawyer plan is introduced. A committed requeue emits only job ID, attempt and the
closed transient error code; no model text, exception detail or credentials.

Retry eligibility must remain explicit at the HTTP boundaries. Untyped or
inconsistent HTTP failures are terminal PROVIDER_INVALID. Core binding/protocol
errors, malformed HTTP protocols, permanent provider 4xx rejections and redirects
use the existing invalid-provider path. Provider 408/429/5xx and transport/wall
availability failures keep their availability classification. Auth, validation,
source/date/lease failures, safe stop, cancellation and internal errors cannot
become a normal retry through a generic unavailable fallback. No timeout, model,
risk rule, legal source approval, role or rate-limit setting changes.

Real PostgreSQL tests exercise the two allowed failures, terminal second failure,
new-token claim and rejected old-token publication, no queued notification,
retained normal/HUMAN_REVIEW receipts, the unchanged three-claim crash budget and
withdrawn actor/source access on the next attempt. One complete `process_job`
recovery uses mocked orchestrator HTTP with real Core report/receipt persistence,
including an uncertain HTTP response after commit. Boundary tests cover permanent
HTTP rejection, protocol failure, cancellation and sanitized error handling.
