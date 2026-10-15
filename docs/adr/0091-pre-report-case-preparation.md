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

The runtime-role schema/constraint/retention tests pass33 cases. Actual empty-data
upgrade→downgrade→upgrade succeeded on a separate disposable database. A downgrade
with retained preparation data refuses transactionally rather than dropping it.
Mutation evidence: installing the previous retention function causes12 failures
(expired/archived/submitted/missing × three states); restoring the expanded
function returns all33 cases to GREEN. The fact-digest guard mutation likewise
caused its tampering regression to fail and was restored, with no mutation committed.

This checkpoint does NOT implement reservation, completion fencing, actor recheck,
replay expiry guards or understanding calls; PRC2 remains incomplete until those
runtime paths are implemented/tested. Neither old readers nor producers are changed
to emit V2. No production schema/configuration/data change is part of this checkpoint.

Migration patterns checked against [Alembic operations](https://alembic.sqlalchemy.org/en/latest/ops.html),
[PostgreSQL16 CHECK constraints](https://www.postgresql.org/docs/16/ddl-constraints.html#DDL-CONSTRAINTS-CHECK-CONSTRAINTS)
and [CREATE OR REPLACE FUNCTION ownership/permissions](https://www.postgresql.org/docs/16/sql-createfunction.html).
