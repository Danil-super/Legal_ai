# ADR0100: Source-bound factual proposals and internal work-product versions

## Status and scope

27 October 2026. AIF01 reproductions and AIF02 inactive contracts/storage proof
implement the approved SPEC-ai-first-resolution.md plan. These changes add no
producer, endpoint, migration, feature flag or production behavior. The storage
proposal below received Checkpoint A owner approval ("продолжай", 27 October 2026)
in reply to the concrete one-table/Alembic request. Production release remains a
separate decision; approval does not mean the table or runtime path exists yet.

## Confirmed problems

The original narrative is already included separately in AnswerUnderstanding's
model context. Its absence from `_CONTEXT_FIELDS` is not a loss of that narrative.
The existing interpretation contract accepts only the current question's value,
anchored only in the latest reply. A rich answer's other facts cannot be staged;
the actual Telegram watcher can consequently ask for a date already in that reply.
Another reproduction reaches MATERIALS rather than summary despite sufficient
facts and an optional upload. Those two acceptance tests remain RED on the
independent reproduction branch; this foundation does not fix either behavior.

Early HIGH/CRITICAL handling correctly creates HUMAN_REVIEW before model reasoning.
It must remain immediate. A populated AI work product must be prepared separately,
without postponing escalation or fabricating a canonical report.

## Inactive typed boundary

`RetainedSourceSegment` represents bounded de-identified retained text, kind,
source ID, draft revision and exact SHA256. Core selects allowed sources; knowing
an ID/hash is not permission to read or use one. `RetainedSourceAnchor` identifies
the exact nonblank character span in an allowed segment.

`FactualContextProposal` carries up to twenty-four distinct existing question identities,
each with a candidate JSON value and one to three source anchors. It has a 16000
character serialized budget. Source validation reparses nested instances, accepts
at most sixteen segments/12000 source characters, rejects unknown/changed sources
and does not confirm facts. Boundary validation is not proof that a candidate is
semantically supported by a quote. Later staging must additionally validate field
semantics, actor/clinic/draft/revision, allowed targets/service scope and conflicts.
Final factual-summary consent remains required. Model fields cannot approve norms,
set risk, change permissions or turn unknowns into safe defaults.

`MaterialQuestion` binds an existing question to a Core-owned reason: safety,
source applicability or action selection. A reason enum alone cannot justify a
question; the selector must derive a real outstanding decision from current facts.

`InternalWorkProduct` is a separate UNAPPROVED lawyer-review draft: work-product ID,
version and immediate parent, clinic/case/operation, fact hash, evidence/date/policy
context, bounded summary, ordered actions, uncertainties and legal-fragment IDs.
No `reportId`, approval state or patient-send command is accepted. Without evidence
context it cannot contain legal-fragment references. Context metadata and valid IDs
do not verify prose; all generated legal claims/actions must still pass the Core's
claim/evidence checks before any future producer persists or displays them.
Article/point titles are resolved from approved applicable fragments, not model
memory. Human review/approval is a later Core-owned operation, not a field the model
may submit through this draft schema. Existing wire/resultVersion2 readers do not change.

Validation errors hide input text. Callers must also avoid logging Pydantic's
structured error payloads, which can include inputs; this is not a logging filter.
Frozen models do not deeply freeze lists: revalidation is mandatory at boundaries.

## Actual existing-storage proof

Tests run against a fresh disposable PostgreSQL database migrated to the existing
head, using the runtime role with NOSUPERUSER/NOBYPASSRLS, not SQLite/owner writes:

- Existing IdempotencyRecord JSONB can retain the exact draft payload and tenant
  RLS hides it from another clinic. This proves physical compatibility, not endpoint
  authorization for a lawyer or a valid public storage API.
- The same actor/scope/key cannot be duplicated. Another actor in the same clinic
  can store the same version key, and an existing payload can be updated. Those are
  valid cache semantics, not a case-wide immutable version ledger.
- Existing preparation lease constraints reject AI_WORK_PRODUCT lease relabelling.
  This does not prevent reusing the separate existing case-analysis job lease.
- Case-linked cache content is removed by the real case-retention function. Existing
  audit rows can retain hashes/version metadata without retaining the draft text.

Source inspection also confirms CaseReport requires a real PDF and serves canonical
report readers. AnalysisRun holds verifier metadata, not draft content. Discussion
messages are human-authored append-only chat, not structured version storage.
These findings do not prove that cache reuse is theoretically impossible: a new
case lock, version allocation, audit protocol and retention binding could provide
additional guarantees. That would complicate a retry cache's unrelated semantics.

## Concrete storage proposal for Checkpoint A

Prefer one additive `case_work_product_versions` table with an Alembic migration:

- UUID row ID; clinic_id, case_id and work_product_id; positive version and nullable
  immediate parent_version. Unique(clinic_id, case_id, work_product_id, version),
  with composite tenant/case and parent foreign keys.
- Actor membership that actually requested/authored the version; server-owned
  origin (AI/HUMAN), review state and reviewer identity. The Agent can submit only
  the unapproved draft contract. Existing authorized human review creates a new
  immutable version; it never changes the original AI version or resolves a case
  merely by creating a draft.
- Bounded schema-versioned JSON payload; content SHA256; fact snapshot, evidence
  trace, clinic-document context trace, reference-example trace when present,
  policy version, as-of date and creation time. All bindings come from Core's
  current authorized context. A model-supplied hash/label is not verification.
- Immutable rows under the runtime role, clinic RLS, guarded version allocation
  under a case lock. The existing analysis job/lease handles background work;
  fencing and replay tests must reject old jobs after factual edits or approval.
- Existing case-content retention also purges these payloads; metadata-only audit
  retains version IDs/hashes under the existing audit retention policy. No new
  personal-data category, external storage, queue, provider or dependency.

Future operations are: obtain an authorized internal draft context; submit a
source-bound verified unapproved draft; read the latest permitted version; create
a human-corrected/reviewed version. They use existing clinic/case/lawyer permissions,
never grant access to all clinic staff or other lawyers' assigned case contents.
No patient-delivery operation is introduced. Their request/response schemas and
authz/revocation tests must be implemented together; this ADR is not an endpoint.

The existing analysis-job result remains HUMAN_REVIEW with the existing escalation
ID. The workspace reads the separate work product. It needs neither a third job
result version nor a fake report ID. Normative approval, current v4 risk policy and
canonical reports/PDFs remain unchanged.

## Verification and remaining work

Inactive contract/source/privacy/version tests and real runtime-role storage tests
pass with the adjacent existing contracts. Removing the new source-hash check makes
its regression fail; restoring it passes. Independent review and static checks are
recorded in tasks/todo.md. No live model performance, complete AI plan, user-visible
fix, release readiness or legal quality is established by this foundation.

After Checkpoint A approval: implement multi-fact interpretation and authorized
staging (AIF03/04), material-question selection (AIF05), then separate draft context,
generation, durable delivery and human review (AIF06–10). All release gates remain.
