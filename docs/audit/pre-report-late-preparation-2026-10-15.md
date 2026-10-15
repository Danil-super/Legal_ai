# Report-free late preparation checkpoint — 2026-10-15

Scope: opt-in internal Core late producer and durable result readers. This is not
the completed Hermes intake/shared Telegram dialogue or a production release.

## Implemented

- `8a86ce9`: Core stores NEEDS_FACTS/LIMITED with actual persisted verifier/risk
  identities and source fingerprints. Partial verified claims plus questions do
  not become READY. Intermediate results create no CaseReport or PDF. Only READY
  invokes the existing source-bound report writer and downloadable PDF.
- `9b4557d`: immutable audit receipt additionally binds scope/key/request hash;
  another legitimate result from the same case cannot replace a replay. Case
  locking/ORM refresh precedes eligibility and replay for both result versions.
  Same-key concurrent requests replay once; distinct keys cannot finalize twice.
- `834a967`: V2 job reads, crash recovery and finish reuse the same audited reader;
  a syntactically valid invented operation is no longer accepted from cache.
- `94a9751`: access is resolved again after case/job waits. Current wall-clock
  subscription expiry and refreshed case retention apply before cached reads or
  new writes. Existing authorization is reasserted, not broadened.

Existing V1 wire shapes, ordinary queue producers, models/provider/high, deadlines,
risk policy, approved legal sources and stored documents/approvals are unchanged.
The earlier planned unreleased migration is not applied to production.

## Verification evidence

- Initial route test was RED (404); writer's first focused PostgreSQL set:44 pass.
- Full immutable initial writer `8a86ce9`:2920 passed/0 skipped,225.81s. Independent
  review subsequently found three missing regressions; that green run is not
  evidence that these later findings were absent.
- Cache substitution, duplicate finalization and invented job-operation tests
  each reproduced the incorrect behavior before its fix.
- Author combined producer/job/legacy clarification/access tests at94a9751:
  94 passed,53.53s. Ruff and all-three-service mypy143 source files passed.
- Independent immutable834a967 verification:9 actual PostgreSQL checks pass,
  including both V1/V2 same/distinct-key requests and a real finish_job/submission
  interleaving. The speculative immediate-retention/job-lock deadlock was
  disproved against the installed function and is not a reported/fixed defect.
- Independent immutable94a9751 access verification:20 pass,35.60s. Besides the10
  committed fresh-submission tests,10 cached-replay cases used actual job lock
  waits confirmed with pg_blocking_pids. Withdrawal/expiry returns403/410 without
  exposing source/report/questions or adding risk/run/report records.

The execution environment reset while the final full run was active. Its result
was not recovered and is not claimed. Committed HEAD94a9751 survived in Git; a
persistent worktree was recreated without changing the user's dirty original.
The repeated full run uses a fresh migrated disposable PostgreSQL database and
local MinIO; JUnit artifact is outside the repository at
`/home/danil/.cache/dental-prc-verify.pLetxJ/94a9751.xml`. Repeated outcome:
**2938 passed,0 skipped,171.27s** (298 pre-existing Telegram warnings).
Ruff, all-three-service mypy143 and Compose validation also pass after recovery.

Code Graph context/reindex was obtained through its real stdio MCP interface for
the exact worktree:567 nodes/566 edges. Configured alternate graph targets another
project and is not evidence here. Python symbol/dependency extraction is limited;
zero-dependency results are not used as proof of isolation.

## Remaining before user-facing completion

PRC6b/6c source-bound recovery/callbacks,
PRC7 report-free initial receipts, PRC8/9 protected Hermes understanding, PRC10
shared confirmed form and PRC11 full-flow acceptance remain open. No claim that
new quick/guided cases already use this complete dialogue. Server rollout requires
the agreed separate immutable-head review/release checkpoint.

## Pre-context gate and transport checkpoint

Core cd7646a and transport f87d41c add the explicit V2 pre-context gate. Unknown or
approximate dates, unknown safety and missing approved evidence yield honest
LIMITED results without a verifier run or PDF; urgent confirmed facts persist a
real escalation. Fresh work respects the operator safe-stop; existing urgent
intake routing remains available. A pre-context operation uses its real durable
receipt ID, not a fabricated analysis identity. Queue fixture prerequisite6cbf2db
now reflects this persistence requirement without weakening assertions.

Review reproduced a direct same-key retry422 after late completion. An authorized
preflight now reads the actual retained, audit-bound late result before marker
comparison. Late writes still compare the full payload hash. Independent real
agent→Core→runtime PostgreSQL tests cover NEEDS_FACTS/READY and safe-stop replay
with each synthetic model stand-in called once, no repeat retrieval/rendering,
tenant/expiry rejection and cache/request/key substitution rejection. Final exact-
artifact review:23 Core +63 agent tests passed, no skips, no remaining Required/
Critical finding in this scope. Review evidence survives outside the repository:
`/home/danil/.cache/dental-prc-verify.pLetxJ/precontext-final-review.md`.

Author focused verification:111 Core +63 agent tests passed. Full immutable code
f87d41c, fresh migrated PostgreSQL and MinIO: **2975 passed,0 skipped,189.68s**;
298 existing Telegram warnings. JUnit:
`/home/danil/.cache/dental-prc-verify.pLetxJ/f87d41c.xml`.
Ruff/all-three-service mypy143/Compose pass. Actual exact-worktree Code Graph
reindex:569 nodes/568 edges; Python impact limitations still apply.

No external provider/model was called, no complete new Telegram intake claimed,
and no server migration, release, normative approval or feature activation made.
