# Report-free late preparation checkpoint — 2026-10-15

## Latest internal understanding checkpoint — 16 October

Initial interpreter a1b3888 is internal only: one supplied existing researcher
client, no provider/model/high/timeout change. Only bounded de-identified source
text enters the model. Existing nine-field proposals require strict schema/source
anchors, nonblank literal non-URL service labels, valid service slots and nonfuture
dates. No file, arbitrary model question, legal claim, risk decision or fact-write
API is added. Offsets and schema do not prove semantic truth; future explicit user
confirmation is still required. INTERPRET_ANSWER and ordinary UI are not implemented
by this slice.

Independent review reproduced protocol-relative IP URL labels, whitespace service
labels and retained Pydantic validation context. All three were treated as
actionable structural/privacy-boundary findings, without claiming SSRF or observed
PII exfiltration. Final45 interpreter tests pass; independent combined regressions
464 passed plus12 own attack probes. Dropping the URL guard kills8 tests; exact
restoration passes45. Report:
`/home/danil/.cache/dental-prc-verify.pLetxJ/prc8a-understanding-review-20261016.md`.

Storage fix14f3755 keeps mandatory nested date:null for UNKNOWN while omitting only
absent root proposals. Both new PostgreSQL regressions initially failed during
completion with missing-date validation errors. The fixed27 lifecycle tests pass
through completion, fresh-session read and reservation replay without draft edits.
Independent107 tests/0 skips include14 own PostgreSQL probes for empty/root-null
and optional nested-null values, access/expiry, unchanged drafts and zero legal
records. Legacy optional-null-omitted caches still read/replay without rewriting;
corrupt required-date omission fails closed. Reverting to the recursive serializer
kills5 tests; the exact fix is restored. No migration, backfill or automatic repair.
Report: `/home/danil/.cache/dental-prc-verify.pLetxJ/prc8-unknown-storage-review-20261016.md`.

Final source/test snapshot: commit **14f3755bf5b4d23df545e27cc8b0a64bf23fd18f**,
tree **67b3aec6c6200af65faac42b463a6221141fb5a6**. Full result: **3192 passed,
0 skipped,493.28s**,302 existing Telegram-library/test-environment warnings.
Fresh migrated parent `dental_legal_test_prc8_final_20261016` on task PostgreSQL55487
and task MinIO were used. JUnit:
`/home/danil/.cache/dental-prc-verify.pLetxJ/prc8-final-full-20261016.xml`.
The preceding a1b3888 full run had3190 passes/0 skips,553.30s, but predates the
storage fix and is not the final combined gate.
Ruff/all-three-service mypy145/Compose pass. Final Core image1e091a69f9c4 and
Agent image3470b0b795e9 build and import their changed modules with networking off.
Gateway's unaffected COPY set was built at the preceding configured-composition
checkpoint. Actual Code Graph refreshed:582 nodes/581 edges, limited Python impact.

A further read-only design review identified pending integration obligations:
private owned lease-context before model invocation, shared existing two-slot
admission rather than extra model pressure, durable cancellation rather than RAM
pause, saving quick-entry text before proposals, sanitized Agent validation errors
and avoiding a circular import. Those future APIs/executors are not present or
claimed fixed. Plan/todo record their order before any ordinary producer activation.

All fixtures/model HTTP/Telegram outputs are synthetic. No live semantic-quality
or full legal-readiness claim; no external CLI/model/provider/SSH call, production
write/release, normative approval or public activation. PRC8b/9/10/11 remain open.

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

PRC6c source-bound Telegram callbacks,
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

## Core intermediate-origin recovery checkpoint

Ordering prerequisite9a6ec75 uses PostgreSQL storage clock under the existing case
lock, not transaction-start time. Independent real A/B blocking test confirms
that an older transaction storing second becomes the latest source. No schema
expansion, timestamp backfill or external I/O was added.

Recovery7c2dbef extends the existing clarification API with paired operation ID/
whole-result hash, exclusive of report origin and omitted for legacy payloads.
The audit-validated latest result must match current facts and be NEEDS_FACTS or
LIMITED. Newer READY/HUMAN_REVIEW and corrupt latest results cannot revive older
origins. Attempted UNKNOWN/APPROXIMATE values and analysis answers survive prefill;
no original case/file is rewritten or copied. Current role/creator/tenant/access/
retention checks apply after actual case/member waits and before replay.

Independent review found a valid same-origin draft substitution via mutable
receipt.resource_id/response_json. New-origin creation/replay now checks immutable
request-bound creation audit. The author reproduction was RED200→GREEN502; normal
replay and resuming user edits remain supported. Legacy report origin/audit shape
is unchanged. No remaining Required/Critical was demonstrated in scoped follow-up.

Verification:

- Author35 new/prerequisite plus64 existing recovery tests: **99 passed**,52.32s.
- Independent exact fixed-artifact follow-up: **41 passed**,41.38s, zero skips.
  Includes own same-origin substitution, missing audit, moved key, changed request
  hash, current edited draft and archived-target checks. Durable evidence:
  `/home/danil/.cache/dental-prc-verify.pLetxJ/prc6b-final-review.md`.
- Earlier uncorrected tree d87bb0b passed3009 tests; it is not final clearance.
- A parallel author test run and reviewer probes on the shared cluster hit
  PostgreSQL `tuple concurrently updated` in ALTER ROLE for the shared runtime
  login. Actual server log identified the setup race. The incomplete full run
  was stopped and is not counted as passing; source/tests were not weakened or
  retried around assertions. Reviewer repeated its checks sequentially.
- Final full run used a separate disposable PostgreSQL cluster on localhost55487
  (`dental-prc-postgres-full-20261015`) plus task MinIO. Immutable source/test tree
  **91764b7974a0ceeca103c43863ed2f8a94c8bed7**, commit7c2dbef:
  **3010 passed,0 skipped,224.57s**,298 existing Telegram warnings. JUnit:
  `/home/danil/.cache/dental-prc-verify.pLetxJ/prc6b-final-isolated-cluster.xml`.
- Ruff/all-three-service mypy143/Compose pass. Exact-worktree Code Graph refreshed:
  572 nodes/571 edges. New material recovery checks use actual PostgreSQL and the
  existing synthetic object-store double; full suite also runs existing MinIO tests.

This completes PRC6b Core recovery only. New Telegram callbacks/shared form,
report-free initial workflow and confirmed Hermes interpretation remain open.
No external model/provider, SSH or production changes occurred in this checkpoint.

## Telegram intermediate-origin recovery checkpoint — 16 October

Codec/client fd68e8f and runtime/router f459747 consume the new origin in a57-byte
canonical callback. Preview and returned draft must match the complete case/fact/
operation/result identity. Stable actor/message/source-derived keys preserve one
audited draft when the Core response or Telegram reply is lost. Legacy report
callbacks remain supported; no fallback to a different origin, automatic fact
confirmation, model request, file copy or report generation is added.

Independent codec/client review:16 new tests and66 legacy tests pass;5 PostgreSQL
cases were skipped in that review only. The author later ran the87-case combined
selection with real PostgreSQL and zero skips after restarting stopped test-only
containers. The first connection-refused setup run is not a pass.

Independent runtime review:134 passed/0 skipped,24.79s on its own PostgreSQL cluster.
Own probes include malformed/future DTOs, actual full application callback routing,
GET200 followed by a newer source before POST (409/no draft/report), and a different
creator in the same clinic (404/no POST). A temporary comparison-guard mutation in
the reviewer-owned tree caused two failures, was restored, and the graph refreshed.
No Required/Critical finding remains demonstrated in this bounded review. Reports:
`/home/danil/.cache/dental-prc-verify.pLetxJ/prc6c-intent-client-review.md` and
`/home/danil/.cache/dental-prc-verify.pLetxJ/prc6c-runtime-review-20261016.md`.
The owner chose these reviews/tests without an external CLI review.

Author runtime/legacy selection:94 passed/0 skipped,19.15s. Full immutable code/test
tree **bb5307c7db4f40ffdd3da57962cf8f1dfd3a5837**, commit f459747:
**3053 passed,0 skipped,313.72s**,299 Telegram library/test-environment warnings.
Fresh migrated PostgreSQL database `dental_legal_test_prc6c_20261016_full` on task
cluster55487 and task MinIO were used; reviewer used separate cluster55486 to avoid
global runtime-role provisioning races. JUnit:
`/home/danil/.cache/dental-prc-verify.pLetxJ/prc6c-full-20261016.xml`.
Ruff/all-three-service mypy143/Compose pass. Actual exact-worktree graph refreshed:
574 nodes/573 edges; Python dependency extraction remains incomplete.

This completes the callback consumer, not shared-form/initial-producer PRC7 or
confirmed Hermes interpretation PRC8–11. No external provider/model was called,
no SSH, server migration, deployment, normative approval or activation performed.

## Explicit form control and report-free initial writer — 16 October

PRC7a b6df566 renders one operation-bound factual control through status, result
delivery and restart notifier. NEEDS_FACTS offers clarification, LIMITED an explicit
manual correction; neither offers a final PDF or automatically repeats analysis.
HUMAN_REVIEW keeps the lawyer route. Author focused73 tests pass, including real
rendered-button→client→Core→PostgreSQL recovery for NEEDS_FACTS and LIMITED.
Independent246 targeted tests pass; the review found an inherited content-validation
gap after checking source identity, which was classified actionable, not ignored.

PRC7b f48320b accepts explicit integer receiptVersion2 for GUIDED_V2 facts only.
Legacy omission and canonical hashes remain unchanged. V2 confirmation persists
the actual facts/receipt without constructing an intake report/PDF. Attempted
UNKNOWN/APPROXIMATE date can be stored when it alone blocks finalization; later
legal analysis still refuses date-dependent advice. Urgent10k/court/hospitalization
routing, metadata-only material move, quotas, creator/tenant, retention and current
access after waits remain enforced. A reproduced urgent-policy-loss condition was
RED201→GREEN409: attempted dates cannot bypass a previously required escalation.

Author writer regression:73 pass on a fresh parent database. An earlier run against
the reused test parent had72 pass/1 legacy search failure because a previous test
left an approved synthetic corpus row. That root legacy suite expects an empty
parent; a new migrated disposable database fixed the isolation issue. No retrieval
code, assertion, test or gate was weakened. Earlier RED implementation runs and
setup/annotation/formatting errors are not counted as passes.

Independent writer review:82 pass/0 skips on separate cluster55486. Its18 additional
probes include actual two-connection wait/revocation/current-clock subscription
expiry, draft natural expiry, concurrent identical/mixed-mode submissions, tenant/
creator reuse, active/monthly quotas, exact provenance/retention and expired replay.
Inverting the attempted-date guard killed three tests; source restored exactly.
No new Required/Critical was demonstrated in this scope. Report:
`/home/danil/.cache/dental-prc-verify.pLetxJ/prc7b-workflow-review-20261016.md`.

PRC7c3868e20 closes the inherited consumer gap. Seven malformed response vectors
were reproduced before the fix. The existing Core partial-draft validator and
CorrectionSource move to already-packaged pure contracts; Core and preparation
consumer use the exact same function. Unsupported/mixed keys, malformed screening,
question/answer schemas and16384-byte UTF8 limit now refuse continuation. This is
not full semantic scalar validation, confirmed facts or a demonstrated auth bypass.
Author related69 tests pass with PostgreSQL. Independent307 pass/14 PG skips;
3522 old/new outcomes and JSON schemas match,39 additional runtime probes pass.
Removing the consumer call fails all seven regressions, restoring it passes them.
No remaining Required is demonstrated in this bounded review. Report:
`/home/danil/.cache/dental-prc-verify.pLetxJ/prc7c-boundary-review-20261016.md`.

Final combined source/test tree **eda5a576ba356ef58315f4869ac6c55a14d27222**,
commit3868e20: **3096 passed,0 skipped,341.59s**,299 existing Telegram warnings.
Fresh database `dental_legal_test_prc7_combined_20261016` on task cluster55487 and
task MinIO were used. JUnit:
`/home/danil/.cache/dental-prc-verify.pLetxJ/prc7-combined-20261016.xml`.
Ruff/all-three-service mypy143/Compose pass. Real Gateway Docker build and composed
entrypoint imports pass (local image dental-prc7-gateway-20261016,3f16a1d04bce).
Actual Code Graph refreshed:576 nodes/575 edges, incomplete Python impact coverage.

PRC7 remains partial: ordinary Gateway omits the new receipt opt-in; its complete
initial confirmation consumer (PRC7d), early Hermes understanding/acceptance and
shared final dialogue (PRC8–11) remain open. Tests use synthetic external-model and
Telegram stand-ins, not live provider quality or production delivery. Models/high,
provider, budgets, risk policy, approved sources and approval authority unchanged.
No external CLI/model/provider, SSH, server migration, deployment or activation.

## Initial consumer and configured-composition checkpoint — 16 October

Client8b5bb30 sends explicit integer receiptVersion2 only on the new intent and
refuses a legacy response. Queue64c4738 derives result_version from the immutable
stored workflow, not a client-selected JSON/header. Current creator/access/expiry
are rechecked after the actual case lock; incompatible active/completed legacy
jobs are not silently reused. Runtimef83a49f binds explicit confirmation/restart to
the same workflow and preserves attempted UNKNOWN/APPROXIMATE dates and screening.
Intermediate presentation creates no intake PDF or false completed-report claim.

Independent review found a future-date regression when the shared calendar model
replaced the old parser. Three own probes were RED; final code retains the existing
future prohibition with approximate-date support. Author added four mode/precision
regressions. A first incomplete fix ignored the parser's None result and remained
RED; it is not counted as a fix. An interrupted full run (1212 passed, exit2) also
does not establish full verification.

The next full run had3140 passes/1 failure in400.79s. Investigation reproduced a
real configured-startup defect: the legacy analysis adapter appended another CTA
after the new presenter, including after urgent lawyer routing. Fixd488314 leaves
versioned navigation with its strictly validating original reader. It does not
reset test globals to conceal the defect. Four standalone adapter regressions were
RED2/GREEN4; both receipt-presentation cases now always build configured startup,
asserting exactly one normal analyze control or one urgent lawyer control.

Independent evidence: queue113 passed/0 skips (including30 own access/race/storage
probes); final combined runtime934 passed/0 skips,72.91s, including the whole
Gateway suite and actual PostgreSQL workflow/retention checks. The latter executes
real queue/worker/orchestrator/clients/Core with synthetic model HTTP and Telegram:
initial receipt2→NEEDS_FACTS/no report→operation-bound recovered draft→explicit
answer/confirmation→new receipt2→READY with verified ACTION and checksum-valid PDF.
Existing UNKNOWN service-date attempts and all original fact rows remain unchanged.
Fresh configured composition separately preserves legacy PDF/analyze controls.
Inverting the new adapter guard kills six tests; exact restoration passes13.
Reports remain outside the repository:
`/home/danil/.cache/dental-prc-verify.pLetxJ/prc7d-queue-review-20261016.md` and
`/home/danil/.cache/dental-prc-verify.pLetxJ/prc7d-runtime-review-20261016.md`.

Final immutable source/test tree **fec864ab986a77cd261cf6ab394fac40161aa166**,
commit **f83a49fb4f51af94151260d72e4137a761c9989d**: **3145 passed,0 skipped,
516.72s**,302 Telegram-library/test-environment warnings. Fresh migrated parent
`dental_legal_test_prc7d_composed_20261016` on isolated task cluster55487 plus task
MinIO. JUnit: `/home/danil/.cache/dental-prc-verify.pLetxJ/prc7d-composed-full-20261016.xml`.
Ruff/all-three-service mypy144/Compose pass. Actual final Gateway image builds and
its entrypoint imports pass: dental-prc7d-composed-gateway-20261016,741386ec5bc2.
Exact-worktree Code Graph refreshed:580 nodes/579 edges; Python impact is incomplete.

Ordinary initial submission remains legacy. Explicit seeded acceptance does not
establish PRC8–11 early understanding, both ordinary entry paths, semantic model
quality or complete user-facing readiness. No live model/provider/CLI/SSH call,
production migration/release, normative approval or activation occurred.
