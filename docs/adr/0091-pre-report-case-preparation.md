# ADR 0091: Report-free case preparation and bounded understanding intents

Date: 2026-10-15. Status: implementation contract, producers disabled.
Scope: approved SPEC-pre-report-intake-understanding.md and PRC1–PRC12 in tasks/todo.md.

## Decision and alternatives

Preparation is not a completed legal report. Introduce an opt-in resultVersion2
discriminated result with NEEDS_FACTS, LIMITED, HUMAN_REVIEW and READY. Preserve
AnalysisSubmissionResponse and all old reports unchanged. Do not return report:null
under the old contract, create fake reports/cases to satisfy FKs, or use gateway RAM
as a durable job store. Hiding PDF buttons alone leaves false report artifacts.

Hermes proposes interpretation of the narrative and later answers; the user confirms
facts, server code enforces transitions, and Legal Core owns legal/risk decisions.
Neither accepting a Pydantic object nor comparing hashes performs authorization,
retrieval, semantic verification, or legal approval. Do not expose these types as an
unauthenticated model-to-Core write API.

## Wire contract

### PRC8a bounded initial narrative interpreter, not activated

IntakeUnderstanding reuses a supplied existing researcher HermesClient for one
tool-free call. It changes no model/provider/high/timeout configuration. Input
must be10–1500 nonblank de-identified characters; local redaction that would alter
the input refuses it instead of silently changing source offsets. Model errors
propagate without retry for the later durable manual-continuation adapter.

The output is only DraftSuggestions, never confirmed facts. Validate the existing
nine-field schema, exact input offsets/nonblank anchors, output privacy, literal
non-URL service labels, existing service-slot scope and nonfuture factual dates.
Retain explicit nested date:null for UNKNOWN while checking scope. Schema/offsets
and the reused scope validator do not establish semantic truth, negate a fact or
approve a legal conclusion. Future explicit user confirmation remains mandatory.
Instructions demand abstention for ambiguity, negation and uncertain course/episode
binding; synthetic transport tests do not prove live model interpretation quality.

This first internal slice has no HTTP entry point, storage write or ordinary UI
wiring. Protected invocation, subsequent answer interpretation with existing
confirmed context, cancellation/revisions and confirmation remain PRC8b/9/10.

### PRC8 storage prerequisite: explicit unknown dates survive persistence

The existing early-operation writer omits only absent root proposals. Recursive
exclude_none discarded required nested date:null for UNKNOWN event/service dates,
so immediate completion failed validation and could not be replayed. Retain nested
nulls, without inventing dates, changing the request intent or accepting proposals
as user facts. Real PostgreSQL tests cover completion, separate-session restart,
reservation replay, stored JSON and unchanged owned draft. No schema/backfill or
activation is needed; service-slot/semantic acceptance is still a later boundary.

PreparationResult is an unwrapped RootModel union discriminated by status; every
variant requires the exact integer resultVersion2 and a strict source:
operationId, caseId, factSnapshotSha256 and explicit context. Before retrieval,
context is null; after retrieval it is a complete asOfDate/evidenceTraceSha256/
clinicDocumentContextTraceSha256/riskPolicyVersion object, never fabricated hashes.
All hashes are 64 lowercase hexadecimal characters. Public envelopes reject
client-supplied tenant/actor fields; server context remains authoritative.

- NEEDS_FACTS: fixed reason FACTUAL_CLARIFICATION_REQUIRED, combined1–3 closed
  clarification requests/missingFactKeys. No arbitrary model question prose.
  Service questions use existing slot constraints; factual scope/attempt checks
  against the retained snapshot remain the producer's responsibility.
- LIMITED: one closed factual/date/safety/evidence reason, optional up to20 unique
  missingFactKeys. No questions, recommendations or report.
- HUMAN_REVIEW: concrete escalationId, HIGH/CRITICAL and nonblank policy version,
  matching context when present. An actual authorized escalation must exist before
  a producer creates this variant; the schema cannot prove that DB relationship.
- READY: actual analysisAllowed:true and typed ReportResponse. Reparse canonical
  report, require READY/PASSED, LOW/MEDIUM, no escalation or pending facts/questions,
  nonempty available recommendations/legal basis, date-applicable source cards.
  Bind case/report/version/run/facts/date/evidence/clinic-context/policy identities.
  Recompute the fact digest using the existing risk helper. The authenticated
  producer must still compare this envelope with its retained operation and actual
  retrieved/verified evidence; a self-consistent payload is not proof of truth.

Early draft understanding has no caseId yet and does NOT use this case-result
union. Its separate bounded proposals are tied to DraftPreparationIntent, never
promoted to confirmed facts merely because the model returned valid JSON.

## Storage feasibility evidence and required expansion

On disposable PostgreSQL16 at migration c4a0c3f6e852, the actual restricted runtime
role persisted/re-read early intents with no case/report in IN_PROGRESS, SUCCEEDED
and FAILED states. Existing unique(scope,actor_membership_id,key) rejected duplicates
inside a savepoint; another clinic could not read the row under RLS. This proves
storage identity/roundtrip/tenant isolation, NOT leases, recovery or privacy cleanup.

DraftPreparationIntent canonically hashes server clinic/actor/draft/revision/input
digest/operation kind. UUIDv5 produces a bounded stable key. Scope draft-prep:v1:<uuid>
fits80 characters; key fits128; raw input is absent from identity metadata. The only
kinds are UNDERSTAND_INTAKE and INTERPRET_ANSWER. Constructing an intent does not
reserve it or authorize its actor. No new table is needed, subject to PRC2 proofs.

Schema inspection confirms the current gap: idempotency_records has no lease fields,
telegram_case_workflows.report_id is NOT NULL, and neither receipt_version nor
analysis_jobs.result_version exists. Existing AnalysisJob has a required case FK;
it is not reused as an early draft queue. The approved additive migration must add:

1. IdempotencyRecord nullable UUID lease_token and timestamptz lease_until, both
   null or both non-null. Non-null leases only for resource_type INTAKE_PREPARATION
   in IN_PROGRESS. Old rows remain valid with null leases.
2. TelegramCaseWorkflow receipt_version NOT NULL DEFAULT1 constrained to1/2;
   report_id nullable only when version2, required when version1. Preserve tenant/
   actor/case/report FKs and uniqueness. Composite report FK remains valid for V1;
   null V2 does not invent a referenced report. No current producer writes V2.
3. AnalysisJob result_version NOT NULL DEFAULT1 constrained to1/2. Do not alter the
   existing case FK, one-active-snapshot behavior or lease/notification contracts.

## Required operation lifecycle before enablement

Reserve the intent atomically under the server's current actor/clinic, validate draft
creator/entitlement/revision/status/retention and snapshot digest, commit the short
transaction BEFORE calling Hermes. Duplicate requests use the same intent. The
independent lease token is a completion fence, not the idempotency key. Keep the
approved existing model deadlines; never hold a DB lock or transaction across I/O.

Completion reacquires the owned row and reauthorizes/revalidates the current draft,
snapshot, token and deadline in a short transaction. Cancel/edit/archive/submit,
revocation, expiry and an old token cannot publish a proposal. An unknown/crashed
call expires to manual continuation; do not blindly re-execute the model. An explicit
new input/revision may create a new intent. Recovery must work after process restart.

Early records use resource_type INTAKE_PREPARATION, resource_id=draft.id. Persist
only bounded permitted proposals/metadata, not a second raw narrative, evidence,
file or model transcript. Recheck access/expiry BEFORE any replay/read/confirmation.
All states, including failed/abandoned work, require scrubbing of response content
and invalidation of leases when the owner expires or is gone; retain non-content
replay tombstones. Submitted/archived drafts and a material-gated parent deletion
must not extend proposal lifetime. Current TELEGRAM_INTAKE_DRAFT-only scrubber does
not cover this resource type; PRC2 must extend it and test restart/replay negatives.

Case-bound preparation uses a separate versioned idempotency namespace from legacy
analysis-submissions and a CASE_PREPARATION case-owned resource. Follow actual case
expiry/purge before replay, not early-draft TTL. Recovery binds retained operation/
result hash, immutable case/fact snapshot and available context; no fake report or
invented verifier run when a pre-context gate has not executed. Implement/test this
producer and its retention together before enabling it (PRC6/6a/6b, PRC11).

## Rollout and rollback

PRC1 is types/identity/tests only. PRC2 expands schema/retention with Alembic and
disposable upgrade/runtime-role/compatibility tests; PRC3–5 add readers before any
new receipt/result producer. Verify legacy idempotency, worker claims, runtime RLS,
draft/material retention and old PDF retrieval after expansion. No production
migration, legal approval, provider call or feature activation occurs in PRC1.

Do not claim b052f40 is a safe rollback after new writes. Pin a tested minimum
reader-compatible revision before enabling producers; disable producers first on
rollback and retain the expanded schema/new records. Downgrade must refuse to
discard V2-only records, never coerce them to fake V1 reports or erase private-data
expiry tombstones. Preserve normative/reference approval ledgers and old reports.

## Verification and framework sources

Contract RED was an ImportError for missing PreparationResult, then GREEN. Further
fact-tampering/source-applicability cases failed before the corresponding guards.
Storage tests use a disposable database and restricted runtime role, not SQLite
or production. Remaining lifecycle/reader/end-to-end proofs belong to later PRCs;
passing PRC1 does not mean the Telegram dialogue is deployed or legally certified.

Implementation uses locked Pydantic2.13.4, SQLAlchemy2.0.52, Alembic1.19.1.
Official references: [discriminated unions](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions-with-str-discriminators),
[RootModel and instance revalidation](https://docs.pydantic.dev/latest/concepts/models/),
[PostgreSQL16 foreign-key constraints](https://www.postgresql.org/docs/16/ddl-constraints.html#DDL-CONSTRAINTS-FOREIGN-KEYS).
Existing nested contract instances are serialized/reparsed at the new boundary;
Pydantic's normal instance acceptance must not bypass nested checks.

### PRC2 additive-schema checkpoint (not the complete lifecycle)

Migration d10a6b3e7f91 implements the three existing-table expansions above. It
prepends proposal scrubbing to the existing draft retention function, preserving
its SECURITY DEFINER owner/execute ACL/search_path and material deletion ordering.
Clearing content/leases covers expired, missing, submitted and archived owners in
all three operation states; a live owned draft is untouched. Tombstones survive.

The runtime-role schema/constraint/retention tests pass34 cases. Actual empty-data
upgrade→downgrade→upgrade succeeded on a separate disposable database. A downgrade
with retained preparation data refuses transactionally rather than dropping it.
Mutation evidence: installing the previous retention function causes12 failures
(expired/archived/submitted/missing × three states); restoring the expanded
function returned the original33 cases to GREEN. The fact-digest guard mutation likewise
caused its tampering regression to fail and was restored, with no mutation committed.

Independent review suggested hardening downgrade against in-flight writes. It now
locks all three affected tables before checking retained data. A two-connection
PostgreSQL regression failed without that ordering, then passed with the lock: an
uncommitted preparation writer blocks downgrade; once committed it is seen by the
guard, which refuses to remove the expanded storage. This is not a reservation/
completion lease test. The historical c4a0c3f6e852 backfill test targets that exact
migration (not future head) and retains its full metadata-equality/content-erasure
assertions; finally restores head. Additive lease columns do not weaken that test.

This checkpoint does NOT implement reservation, completion fencing, actor recheck,
replay expiry guards or understanding calls; PRC2 remains incomplete until those
runtime paths are implemented/tested. Neither old readers nor producers are changed
to emit V2. No production schema/configuration/data change is part of this checkpoint.

Migration patterns checked against [Alembic operations](https://alembic.sqlalchemy.org/en/latest/ops.html),
[PostgreSQL16 CHECK constraints](https://www.postgresql.org/docs/16/ddl-constraints.html#DDL-CONSTRAINTS-CHECK-CONSTRAINTS)
and [CREATE OR REPLACE FUNCTION ownership/permissions](https://www.postgresql.org/docs/16/sql-createfunction.html).

### PRC2 runtime lifecycle and bounded proposals

The reusable lifecycle now reserves, reads, completes and fails early operations
using existing draft/idempotency rows. Every call resolves the current actor and
entitlement; creator, clinic, active status and retention are checked before content.
Consistent draft→operation locking and atomic INSERT ON CONFLICT deduplicate one
actor/draft/revision/content/kind intent. Transactions commit before returning a
model claim; only the first reservation receives the180-second completion token.
An expired/failed call offers manual continuation and is never automatically
reclaimed. A different explicit draft revision/input creates a different intent.

Completion checks token/deadline plus the current revision, full draft/wizard
fingerprint, owner and access again. Edits, archive, submit or expiry cannot publish
stale suggestions. Invalid payloads roll back without changing confirmed data.
DraftSuggestions is deliberately separate from draft facts: nine allowed existing
fields, bounded choices/dates/services/screening/clarification answers, no narrative
copy, legal text, actor/clinic, URLs or confirmation flags. Every non-null field
requires1–3 source offsets within the actual de-identified1500-character input.
Offsets prove an anchor, not semantic entailment; PRC8 adds model interpretation
and PRC9/10 add authorized explicit acceptance and service-scope checks. No model
proposal becomes a USER_STATEMENT by persistence alone. Closed PII-free audit codes
record reservation/proposal/manual failure counts, not input or model output.

The new Python proposal contract is a supporting PRC2 split, not another schema or
storage expansion. There are no new tables, HTTP routes or enabled model producers.
This shared service does not replace the later HTTP authz/race/acceptance tests.

Independent runtime review reproduced two actionable failures before activation.
Duplicate reservations now use the same intent/resource/source binding checks as
reads and completions; four retained-row tampering cases failed before the fix.
The unreleased d10 migration candidate also makes retention lock expired draft
owners before operation rows, matching the runtime parent→operation lock order.
A real two-connection regression reproduces expiry while completion holds the
parent and retention is waiting; completion must refuse with410 and cleanup must
finish without40P01, erasing the retained proposal. This edits an unreleased
migration candidate, not deployed migration history.

After locks are acquired, actor membership, entitlement and draft expiry are
checked again. Entitlement deadlines use clock_timestamp(), not transaction-start
now(); natural expiry while waiting must not permit a stale completion. Tests also
withdraw membership or suspend access between the initial check and completion.
No access rules or subscription terms are changed by these repeated checks.
Lock ordering follows [PostgreSQL deadlock prevention](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-DEADLOCKS).

### PRC3 receipt readers, before report-free producers

The existing TelegramWorkflowResponse remains unchanged and report-required.
TelegramPreparationReceipt is a separate strict-shape variant with mandatory exact
integer receiptVersion2 and no report field, even null. Core workflow reads and
submission replays accept both variants; ordinary submission still produces its
legacy report. Database receipt_version chooses the variant, never the presence
of a malformed/missing report. Current tenant, creator, entitlement and case-content
retention checks precede reading either receipt.

Gateway submission/recovery validates the same union and requested workflow ID;
legacy report caseId must equal the returned case. Unsupported versions, partial
payloads, null/fabricated report fields and identity mismatch become a bounded
INVALID_LEGAL_CORE_RESPONSE, not user-facing validation details.
Actual PostgreSQL API tests read a V2 row with zero CaseReport artifacts and reject
foreign clinics, another creator, revoked membership and expired content. Legacy
submission/replay and gateway transport remain covered. This is transport reader
compatibility only: the Telegram report consumer is not yet a V2 dialogue, so this
checkpoint is NOT the minimum rollback revision for enabling report-free producers.

### PRC4 queue readers and retained-result reconciliation

AnalysisJobResponse remains the legacy shape. PreparationJobResponse uses an
explicit exact integer resultVersion2 and typed PreparationResult, with coherent
terminal state and matching source/case. The saved job version selects the cache
namespace: legacy cases:<uuid>:analysis-submissions versus V2
cases:<uuid>:preparation-submissions. V2 also requires CASE_PREPARATION/resource_id
equal to the actual case. No legacy cache is silently reinterpreted as V2.

Workers reconcile retained intermediate results as SUCCEEDED without another model
call and keep existing lease/notification ACK fencing. Invalid cache content raises
a bounded error and is quarantined as a deliverable terminal failure rather than
an endless RUNNING job or repeated parsing failure. A missing V2 success payload is
shown as ANALYSIS_RESULT_MISSING, not a fake report. Reads still enforce current
actor, clinic/creator and case retention before exposing content. The result's
operation/evidence/fact bindings to persisted preparation artifacts remain PRC6/6b
producer/recovery requirements, not claims made by this syntactic reader.

Gateway validates versioned envelopes, preserves NEEDS_FACTS/LIMITED/HUMAN_REVIEW
as intermediate statuses and does not offer a PDF for them. Only validated READY
is adapted to existing final-report presentation. Stale PDF callbacks re-read and
refuse intermediate results. Existing notifier delivery/ACK/retry and legacy PDF
paths remain covered. Source-bound clarification controls are added in PRC6c/10;
no legacy report-based clarification button is invented without a report. No V2
job is enqueued by the current producer; request transport/production activation
are later PRC5–12 steps. This is not a complete user-facing dialogue release.

### PRC5 opt-in authenticated late-result transport

The existing internal analyze route defaults to legacy result version1. Exact
header X-Analysis-Result-Version:2 selects the preparation transport; unknown
versions fail validation before context or model calls. Internal authentication,
paired job lease headers and idempotency binding are unchanged. The shared115s
wall deadline, streaming65s Hermes stages, models/providers/high and verifier
remain unchanged. No classification of arbitrary errors as missing facts occurs.

The typed Core client sends V2 reasoning to the separate preparation-submissions
path and reads PreparationResult, preserving the context fingerprints/date/policy.
A late result must match its actual retrieved context and cannot return context:null.
Both context retrieval and result reads reject a different case. Version2 never
silently downgrades to an old report. Pre-context stops require the future Core
producer/retention/recovery paths in PRC6a/6b; this transport does not fabricate one.
These endpoints are not called by current V1 producers; Core's V2 writer remains
unimplemented until the next task and no new production traffic is activated here.

Framework versions are pinned in requirements.lock: FastAPI0.141.1,
Pydantic2.13.4 and SQLAlchemy2.0.52. Framework patterns were checked against official
[FastAPI union response models](https://fastapi.tiangolo.com/tutorial/extra-models/#union-or-anyof),
[Pydantic TypeAdapter](https://docs.pydantic.dev/latest/concepts/type_adapter/)
and [discriminated unions](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions-with-str-discriminators).
The FastAPI page contains an inconsistent older paragraph about the Python3.10
union operator; the installed pinned version's actual API tests establish support
for the alias-based union used here. No framework upgrade or dependency is added.

### Container-boundary regression and correction

The full d30b8a9 run failed two isolated gateway packaging checks. New transport
imports had accidentally pulled backend API/queue modules into a deliberately
minimal Telegram image. The fix does not copy the server/ORM into that image:
shared CaseResponse/ReportResponse/LegalFragmentResponse and workflow receipt types
now live in contracts.py with identity-preserving api_contracts re-exports.
PreparationJobResponse lives in analysis_contracts, re-exported by analysis_jobs.
Seven public JSON-schema fingerprints were compared against immutable d30b8a9 and
are byte-equivalent after sorted canonical JSON serialization. Wire semantics do
not change; no stored data or migration is involved in this relocation.

Docker explicitly includes the pure preparation/analysis contracts and existing
fact-digest dependencies. Reading READY recomputes a fingerprint; it does not run
the risk policy in Telegram. The isolated copy-set test now imports the optional
analysis runtime as well as the entry point and asserts no case_api, analysis_jobs
or ORM models are available. Missing-module negatives remain enforced. The actual
Docker build also imports both entry points as the unprivileged app user and
passes. This packaging regression is recorded rather than omitted from the audit.

The PRC5 worker follow-up reads result_version from its stored, currently fenced
job and forwards the V2 header only for V2. It checks the existing lease before
orchestrator I/O; the short transaction ends before that I/O. Cached committed
results still reconcile without a model call. A second connection can replace a
token after the job was loaded: the FOR UPDATE query must populate_existing so
the ORM identity map cannot authorize using stale attributes. Tests reproduce
wrong token, expiry, terminal state and the already-loaded-row replacement before
fixing them. No lease duration, actor permissions, model or deadline changes.
The ORM refresh follows [SQLAlchemy2.0 Populate Existing](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#populate-existing).

### PRC6 late preparation producer checkpoint

The separate internal preparation-submissions route reuses deterministic context
freshness, approved/date-applicable retrieval, question-scope/attempt checks, risk
assessment and persisted verifier runs. Existing analysis-submissions keeps its V1
response and report behavior. New V2 writes require the case creator in an existing
intake role; another clinic actor, including an owner, cannot use a guessed case
or idempotency key to write/replay this preparation. A fenced job's saved result
version must match the selected endpoint.

NEEDS_FACTS and LIMITED persist CASE_PREPARATION under the case-owned V2 namespace
with real fact/context/run identities, but never call report/PDF rendering. Partial
verified claims plus pending questions do not become READY; no claims/questions
is a limitation, not an empty final report. HIGH/CRITICAL selection requires the
actual escalation ID and outranks clarification; actual pre-context urgent routing
is the next PRC6a checkpoint, not claimed covered by the late low-risk tests.
Only READY uses the existing real report/PDF writer and then validates the strict
source-bound READY contract before committing. Old stored reports are not changed.

An atomic audit receipt binds operation ID and canonical result digest to the case
and actor without narrative/model transcripts. V2 replay revalidates this receipt,
cache resource/case and shape; a mutable cache alone cannot replace the original
result. Runtime case purge already deletes case-owned idempotency resources in all
states, so no new migration/table is required for this resource kind. Real expiry
and purge/replay tests verify deletion and410 without using production data.

Focused PostgreSQL proof:10 new late-preparation tests plus existing clarification
submission/API regressions total44 passes; all-three-service mypy143 and Ruff pass.
The initial RED is404 for the absent new route. New producer remains unused by
ordinary V1 queues. Pre-context limitations, source-bound recovery, initial
report-free receipt producer and Telegram shared form/Hermes interpretation are
still subsequent PRCs; this checkpoint is not a release or complete new dialogue.

Independent review of8a86ce9 reproduced same-case cache substitution and concurrent
double finalization. The audit receipt now also binds a canonical digest of the
scope, idempotency key and request hash. A valid receipt from another operation
cannot authorize the original replay. Submission locks and refreshes the case
before job fencing, replay and eligibility, so an ORM identity-map snapshot cannot
conceal another committed finalization. The existing scoped FOR UPDATE helper now
uses populate_existing. This fixes both V1 and V2 without changing either wire
contract: concurrent identical keys replay once; distinct keys cannot both finalize.
Both reproductions were RED before fixes;15 focused PostgreSQL tests pass. The
unmodified8a86ce9 baseline separately passed2920 full tests; it is not the final
reviewed/fixed snapshot and that earlier green run does not invalidate the findings.

The third review finding was a weaker V2 queue reader: a shape-valid invented run
identity bypassed the producer's audit validation. All V2 job reads, worker finish
and crash reconciliation now use the same immutable, request-bound receipt reader.
Bad receipts are bounded ANALYSIS_RESULT_INVALID and quarantined without another
model call. Existing synthetic reader fixtures explicitly include a trusted audit
receipt; they no longer imply that mutable JSON alone establishes trust. A real
leased producer-to-job-reader roundtrip accepts the original and rejects a changed
operation ID. The new operation-tampering regression was RED before the correction;
82 focused producer/queue/legacy tests and the additional integration pair pass.
The proposed immediate-purge/job-lock cycle was disproved on the actual migrated
function and is not claimed as a defect or fixed issue.

A submission reauthorizes the same actor after case/job lock waits and checks the
entitlement against clock_timestamp, not the transaction's now(). Membership/user
withdrawal, suspension and natural expiry cannot reuse the earlier resolution;
the refreshed case retention guard is checked again. This reasserts existing
permissions, not a new access policy. Tests pause a real HTTP submission at its
case-lock boundary while a second connection withdraws access or expires content,
for both V1 and V2. Membership/user/natural-expiry cases were RED before the fix;
the suspension fixture was corrected from unsupported REVOKED to actual SUSPENDED
without changing its expected403. All10 boundary tests now pass; combined producer,
job-reader and existing analysis/clarification regressions total94 passes. No raw
case content is returned or report/run written after a denied request.

### PRC6a Core gate before legal context

The opt-in internal POST /v1/cases/{case_id}/preparation-context reuses the same
creator/tenant/role/retention/current-access and paired job lease/version checks
as V2 submission. It performs no external I/O. A usable actual legal context
returns200 without reserving the late submission key. Otherwise only explicit
INSUFFICIENT_FACTS, uncertain-date, unknown-screening and absent-approved-evidence
Core blockers become LIMITED; auth/policy/safe-stop/internal/invalid-screening
errors remain errors. Attempted UNKNOWN/APPROXIMATE facts are retained, not coerced
to negatives or changed to exact dates. No report or verifier run is invented.

Before retrieval, source.context is null and source.operationId is the actual
persisted IdempotencyRecord.id. The same request-bound audit receipt protects
replay and queue recovery; the reader also checks this early operation identity.
Confirmed HIGH/CRITICAL under the existing approved policy is evaluated before
optional date/screening/evidence gates, records the real risk/escalation and returns
HUMAN_REVIEW with its actual ID. The existing no-legal-evidence early-triage trace
is used only for risk persistence, never passed off as retrieved legal context.

The pre-context intent uses the same V2 case-owned namespace/job key with a fixed
PREPARE_ANALYSIS_CONTEXT request digest. A successful context read stores no row,
so the later reasoning submission can use that key. A retained pre-context stop
replays without another model call; a mismatching late submission cannot overwrite
it. Existing V1 GET/context and report contracts remain unchanged. The orchestrator
does not opt in until the following transport increment; ordinary queues remain V1.

Tests deliberately freeze API-validated synthetic fact rows at the future V2
initial-producer boundary: legacy finalization still rejects unknown dates and is
not claimed changed here. UNKNOWN, approximate dates, unknown safety, no evidence,
actual urgent routing and valid context→late submission are exercised through
real HTTP/runtime-role PostgreSQL. The absent endpoint was RED before adding it.
One older queue fixture had context:null with an unpersisted random operation ID;
its row ID is now aligned to that source. The expected success/ACK behavior and
production identity check are preserved, not weakened to make the test green.

Review found that early urgent preparation could bypass the operator analysis
safe-stop. The new operation now checks require_analysis_running after an allowed
retained replay but before policy/risk work, just as V1 stops new automated work.
The regression reproduced201 instead of503 before this correction. This does not
disable the separate existing human workflow: a real urgent intake finalization
under the stop still persists its early escalation, while the subsequent new
preparation returns503 without another risk assessment, verifier run or report.
Both sides are covered; the stop is not reclassified as missing facts.

The independent retry probe found a real422 after the late result committed:
the payload-free preflight marker and late reasoning payload have different
request digests under the same intent key. A repeated preflight is now an
authorized read of an existing SUCCEEDED result, through the unchanged immutable
request-bound receipt validation, before the new-operation marker comparison.
It can return the actual retained late NEEDS_FACTS/LIMITED/HUMAN_REVIEW/READY with
its genuine retrieved context. A NEW pre-context stop still has context:null and
an actual receipt-row operation ID. The late reasoning write still compares its
exact payload hash; no caller can replace that payload or overwrite a receipt.
The real context→late NEEDS_FACTS/READY→same-key preflight tests were RED422 and
now return200 with byte-equivalent canonical JSON and no second model/run/report.
Existing worker reconciliation remains in place.

Bounded NEEDS_FACTS before context remains permitted by the approved generic wire
and plan (pre-context LIMITED or NEEDS_FACTS). Current fresh Core gates emit only
LIMITED/HUMAN_REVIEW; the generic reader's NEEDS_FACTS support is not a legal answer
or a protocol bypass. A review request interpreting it as a forbidden status
misread that contract; no blanket status prohibition is introduced.

### PRC6a authenticated orchestration transport

Only an explicit resultVersion=2 analysis invokes preparation-context before the
existing two-pass reasoning. A genuine usable context continues through unchanged
researcher/reviewer and late submission. A strictly validated, same-case retained
PreparationResult returns directly without a model call. Fresh pre-context limits
for uncertain dates, uncertain safety or missing evidence require context:null;
late retained outcomes may carry their real retrieved context, including READY's
fully validated canonical report. No context or final answer is reconstructed by
the transport. V1 still uses the existing GET context path.

Actor, stable intent key and paired job lease headers are forwarded unchanged.
The existing shared115s deadline and65s stage budgets, models/provider/high, risk
policy and legal verification are unchanged. Core errors remain errors rather
than a synthetic limitation. Ordinary queue creation remains V1: this change does
not activate the new dialogue or modify production settings.

Independent final follow-up verified the exact Core/transport artifacts on its
own branch and PostgreSQL:23 Core plus63 agent tests passed, zero skipped. Real
agent→Core retries of NEEDS_FACTS and READY invoke each synthetic external model
stand-in once; replay under safe-stop performs no retrieval, verifier or rendering.
Cross-tenant access, retention expiry, request/key/cache substitution and changed
late payloads remain rejected. No actual external model/provider was called.
Author focused integration totals111 Core and63 agent tests; the full immutable
regression run is recorded separately in the audit checkpoint.

### Preparation source ordering prerequisite

Case-owned preparation writers already serialize under the case lock. Their
receipt.created_at is now assigned from PostgreSQL clock_timestamp at storage,
not the transaction-start default now(). Otherwise an older transaction that
waited for the case could commit a fresh result ordered before its predecessor.
Source recovery orders these receipts by(created_at,id), then validates status
and origin; it never filters out a newer final/human result to revive older work.
This is not a schema expansion or a legal/evidence timestamp. Existing receipts
are not rewritten. The actual runtime-role PostgreSQL test starts an older
transaction, waits, stores and reads back its receipt; it was RED on the default
transaction timestamp. Normal production writers hold the case lock; the test
directly exercises storage with a synthetic result only.

### PRC7d3 initial Telegram preparation consumer

The new explicit confirmation callback case:confirm2:<workflow UUID> binds the
receipt mode in the durable Telegram message. Core also hashes receiptVersion in
the submission. A retry uses the same workflow/facts/mode; restart recovery only
GETs that workflow and refuses a legacy response for this intent, never creates
another case or falls back to a legacy report. Existing case:confirm remains1.
The ordinary wizard still emits the legacy button until PRC10 activation readiness.

The new consumer checks all ordinary missing fields but preserves attempted
UNKNOWN/APPROXIMATE dates and UNKNOWN screening, rather than reopening them on
confirmation. Invalid calendar/precision values still need correction. Shared
partial intake completeness now accepts a validated approximate date, displaying
"примерно" in the recap; the legacy final confirmation and Core's exact-date legal
gate remain in place. This does not turn an approximate date into an exact one.

Typed receipt2 is presented as source facts with one next step: case processing,
or the actual early-escalation lawyer card. It never downloads an intake PDF,
claims a final answer exists, or assumes no final answer exists on a later read.
Status/result delivery uses the previously implemented preparation reader. Telegram
delivery failure preserves the intent for explicit retry; archive failure reports
saved facts, not a fabricated finished report. No model/provider settings change.

### PRC7d2 queue version follows the stored initial receipt

For a case created by the explicit report-free initial writer, enqueue derives
result_version2 from that case's tenant-scoped stored Telegram workflow receipt.
The request shape remains messageId only: the client cannot choose this mode or
downgrade it, and ordinary/legacy cases still produce version1. The existing
worker forwards the stored version into the authenticated preparation transport.
No global flag or per-request model settings are introduced.

An active/completed incompatible legacy job for a preparation case is a409
ANALYSIS_RESULT_VERSION_CONFLICT, not cached legacy success or a second active job.
Before new/replayed V2 work, the case is refreshed and current actor/entitlement/
retention rechecked after the case-lock wait. Access policy itself is unchanged.
Tests cover actual initial confirmation, deduplicated queue, fenced worker, real
orchestrator/Core HTTP boundaries and SQL; pre-context limits skip the model and
produce no intermediate report. Ordinary Gateway confirmation is not activated.

### PRC7d1 explicit Gateway submission mode

LegalCoreClient.submit_workflow adds optional receipt_version2 with exact integer
and v2 schema validation before HTTP. Omission preserves the legacy request body;
the existing read/recovery client still accepts either stored receipt version.
An explicitly requested2 may not accept a legacy report response as success.
This is transport support, not a switch of ordinary producers. Stable callback
intent and the initial presentation/queue consumer follow before activation.

### PRC7c returned-draft validation before continuation

Independent PRC7a review demonstrated that matching source/ID/revision/state alone
could still offer continuation for a malformed returned draft. The actual Core
write validator refused the same payloads. This is an inherited consumer boundary
gap, not demonstrated persistence/authorization bypass, but must close before
activation of the new form control.

Move the existing bounded partial-draft data validation and CorrectionSource type
to the already-packaged pure contracts module. Core's write schema delegates to
that same function; its existing allowed keys, partial safety/question/answer
validation and16384-byte UTF8 limit are preserved, not independently redefined.
The preparation consumer validates it before offering continuation. Existing Core
imports of CorrectionSource remain re-exports of the exact type. No Core API/ORM
module or new dependency is added to the Gateway image. This boundary does not
turn partial draft fields into confirmed facts or replace later access checks.

### PRC7b opt-in report-free initial workflow writer

The existing submission API accepts an explicit integer receiptVersion2 only with
GUIDED_V2 facts and the v2 intake schema. Omission preserves the exact legacy
canonical request/hash and report-producing behavior; explicit null, coercions
and unsupported versions are rejected. New and old modes cannot reuse a workflow
identity with a different canonical request. The planned existing-table migration
already permits a v2 receipt without report_id; no additional schema is introduced.

Only the explicit mode bypasses initial intake report/PDF construction. It preserves
confirmed immutable facts, correction-source provenance, material metadata movement,
quotas, retention and early triage. Report-created audit is emitted only when a
report exists; v2 workflow audit carries receiptVersion, not a fake reportId.
Finalization audit says PREPARATION_PENDING rather than diagnosing the corpus.

A provided, validated UNKNOWN/APPROXIMATE event date may be saved in this preparation
mode when it is the sole missing fact. An absent date/other missing facts retain the
existing refusal unless approved urgent routing applies. The exact-date requirement
for time-dependent legal advice is unchanged: the actual preparation-context gate
returns ANALYSIS_DATE_UNCERTAIN without a report. Previously confirmed urgency must
still persist an escalation; a changed/unavailable policy rolls back instead of
using attempted_date to suppress routing. Policy and the10k threshold are unchanged.

V2 creation and replay recheck current actor/entitlement after workflow/quota waits,
using the existing current-clock preparation access guard. An owned locked draft's
natural expiry is checked again after the quota wait. Ordinary Gateway submission
still omits receiptVersion: this writer is not production activation or proof of
the complete PRC10 initial confirmation consumer.

### PRC7a explicit form control for intermediate queue results

The validated NEEDS_FACTS presentation offers one operation-bound "Уточнить факты"
button; LIMITED offers an explicit "Изменить сведения" correction, not an automatic
repeat of UNKNOWN/APPROXIMATE questions. Status refresh, result delivery and restart
notifications use this same presentation and the PRC6c consumer. No intermediate
PDF/result button, automatic model retry or claim that editing cures unavailable
evidence is introduced. HUMAN_REVIEW keeps the lawyer path without an optional
clarification diversion; READY retains the existing actual final-report controls.
Presentation additionally rejects a cross-case source even if called with a
manually built JobSnapshot instead of its validated wire parser.

Real Gateway-client/Core/PostgreSQL tests start with the rendered NEEDS_FACTS and
LIMITED controls and recover an owned, source-bound durable draft, preserving the
original facts and report-free case. Telegram network/model output remain synthetic.
This is a consumer change only: ordinary initial submissions and queue producers
remain legacy until PRC7/10/11 compatibility and acceptance are complete.

### PRC6b report-free clarification origin

ClarificationDraftRequest/Preview add paired expectedOperationId and
expectedOperationSha256, mutually exclusive with report origin. Absent optional
fields are excluded, preserving old canonical request hashes. GET preview accepts
explicit operation_id; an unavailable preparation never falls back to an old report.
Legacy report-bound recovery retains its existing shape and prefill behavior.

Under the creator/tenant case lock Core selects the latest SUCCEEDED receipt in
the case preparation scope across all statuses, ordered by storage clock and UUID.
The existing immutable audit reader checks actor/resource/operation/full-result
binding; a late source operation is its real verifier run, not the receipt row ID.
Frozen facts must match. Only NEEDS_FACTS/LIMITED can recover: a newer READY or
HUMAN_REVIEW closes the older origin. Actual question scopes are revalidated.
No new retrieval/policy evaluation occurs; the result hash binds its real context.

Confirmation validates current origin/fact/result hashes before replay too. Access
and retention are rechecked after case/member lock waits using the current clock.
The immutable correctionSource carries case/fact/operation/result hashes. Prefill
deep-copies existing factual fields and attempted UNKNOWN/APPROXIMATE/analysis
answers without moving/copying attachments. Actual questions select
ANALYSIS_CLARIFICATION; absent date/incomplete safety select that existing step;
otherwise V2_CONFIRM allows explicit correction, never legal READY.

Creation replay reads the owned durable draft through the receipt resource ID,
not cached factual text. Malformed/mismatched cached origins fail bounded; expired
or closed drafts cannot replay. Active edited drafts resume their current revision
instead of overwriting edits with an old cache. Audit holds only IDs/hashes/counts.
Telegram callbacks and the initial report-free producer remain subsequent tasks.

Independent review reproduced same-origin draft substitution through mutable
creation receipt.resource_id/response_json. Operation-bound creation now adds an
immutable audit digest of scope/key/request hash, and replay checks that exact
creation event plus its case/fact/operation/result identities. A different valid
draft for the same source cannot stand in for this intent. The author regression
was RED200 with the substituted draft, then GREEN502 with no source/draft changes.
The current edited active draft remains authoritative; legacy audit shape stays
unchanged. Earlier full3009-pass evidence predates this correction and is not the
final fixed snapshot.

### PRC6c compact preparation intent and client

Intermediate-origin buttons use case:clarify2: plus the same canonical base64url
pair of case UUID and actual operation UUID (57 ASCII bytes). Report callbacks
retain case:clarify: unchanged; neither decoder accepts the other's namespace.
The full result hash does not fit in the button and is obtained only from Core's
authenticated, audit-validated exact-operation preview. Creation forwards that
whole hash/fact hash and checks the returned draft's exact immutable origin.
A source cannot silently become a different operation, old report or latest case.

Gateway client selectors and paired operation ID/hash are additive and exclusive
of report origin. They preserve the caller's actor/idempotency key and the legacy
wire shape; malformed/mixed bindings reject before HTTP. This first contract
increment does not add visible buttons, change access, call models or activate V2.

### PRC6c Telegram recovery consumer

The existing clarification handler/router also consumes canonical case:clarify2:
buttons. A dedicated preparation path requires the exact four-field Core preview,
matching case/operation and full lowercase fact/result hashes. It forwards those
bindings to creation and validates the returned draft's immutable correctionSource
and ordinary V2 draft shape before offering resume. It never selects another
operation or legacy report as fallback, copies files, or calls the model/PDF path.
Legacy report callbacks, keys, messages and client shape remain intact.

One new button/message is one intent: UUID5 clarification:v3 includes actor, case,
fact hash, operation ID, whole result hash and original message ID. Repeated clicks
and gateway restart preserve the same key; different messages remain distinct
intents. Unknown HTTP outcomes are not automatically retried. The user can click
again to recover Core's audited result/draft. Stale, invalid and expired sources
show bounded instructions to open the current case, not an automatic repeat loop.
Successful creation uses the existing durable-draft resume controls and explicit
final confirmation. A new legal report is not implied by opening that draft.

Actual Gateway client→Core→runtime PostgreSQL tests cover late NEEDS_FACTS/LIMITED,
lost Core/Telegram responses, restart, exact stable keys, one new draft, creator/
tenant refusal, unchanged original facts/runs/reports and a context:null uncertain
date that remains attempted UNKNOWN. These seed the agreed frozen V2 boundary;
they do not claim the ordinary initial producer or shared confirmation is changed.
The routing consumer is established before any new visible producer is enabled.
