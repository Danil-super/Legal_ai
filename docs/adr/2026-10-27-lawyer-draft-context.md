# Private context for an AI draft reviewed by a lawyer

Status: accepted for the owner-approved AIF06 implementation; disabled by default.

An urgent case is already routed to lawyers by the retained preparation transaction.
Reading additional context must neither delay that routing nor enable a canonical
automatic answer. This boundary supplies deterministic, approved evidence for a
future internal unapproved draft. It does not produce, approve, or publish a draft.

`legal_core.lawyer_draft_contracts` defines `LawyerDraftContextRequest` and
`LawyerDraftContextResponse`. The metadata-only request binds the actual retained
preparation operation, fact snapshot and risk-policy version. The response schema
is `lawyer-draft-context.v1`: source, escalation ID, HIGH/CRITICAL risk and an
`AnalysisContextResponse`. Its source contains the actual preparation operation
and the newly retrieved date, legal-evidence and clinic-document traces. The
response validator rejects source/context mismatches. Caller facts, files, tenant,
approval commands and an `analysisAllowed` override are not accepted.

The private POST endpoint requires the existing Agent key and Telegram actor plus
the running result-version-2 analysis-job lease, with job ID as idempotency key.
Only the current case creator and job owner may read it. The reusable session
loader uses Case → Job → Escalation locks and checks current membership,
entitlement, retention, the audited retained HumanReviewResult, unchanged facts,
current approved policy and an unresolved escalation. The writer can reuse this
loader after model I/O before storing an internal draft. Exact event date and
APPROVED date-applicable evidence remain mandatory; unavailable context is an
explicit safe error and leaves routing untouched. Audit contains IDs and hashes.

`AI_FIRST_LAWYER_DRAFT_ENABLED` defaults to false. This contract does not expand
lawyer access, change risk policy, relax canonical report eligibility or expose
case-material files. No model, producer, storage writer or UI is part of AIF06.

Implementation: `load_lawyer_draft_context(AsyncSession, ...)` returns a
`LawyerDraftContextState` containing the typed response, `AnalysisState` with
domain fragments and current actor. It adds the read audit but does not commit;
the HTTP adapter commits, while a later writer can reuse all checks within its
own transaction after model I/O. The normal context still enters through its
original eligibility gates before the shared deterministic retrieval helper.

Validation uses disposable PostgreSQL and actual HTTP workflow confirmation,
job claiming, audited human-review preparation and queued lawyer notifications.
Tests cover creator/tenant/key/lease, source tampering, facts/policy/retention,
unknown date and unavailable evidence, real Case/Job lock waits, and access or
policy withdrawal after actual retrieval. Disabling the creator guard makes the
creator-change lock-wait test fail. Personal composition remains isolated.

Final source freshness is transaction-owned. Independent review reproduced a
retired clinic contract remaining in the returned context when its approval was
withdrawn after retrieval. The same race existed for the legal editor whose
active role makes a normative approval current. `require_lawyer_draft_sources_current`
now locks only the selected provenance in stable ID order, then re-reads the
approved, date-applicable selected fragments and verifies their complete content
and exact evidence/context traces. It neither substitutes newly retrieved
evidence nor commits. Legal versions precede source/document/reviewer locks,
matching the existing legal approval version-first order. Clinic versions use
`FOR UPDATE`: approval-event INSERTs require the conflicting foreign-key
`KEY SHARE` lock, whereas `FOR SHARE` would not fence those INSERTs. Row counts
are bounded by the existing retrieval limits (30 legal and 20 clinic fragments).

Locks last until the caller's transaction ends. Model/network calls must finish
before invoking this final guard; the loader rechecks actor, entitlement,
retention, lease, facts, policy and operator flags after the new possible waits.
This helper is reusable by the later internal writer/view/review boundary, whose
own authorization and case/escalation locking remain mandatory. It verifies
the selected provenance, not whether a newly added document would be relevant.

Regression tests exercise real HTTP retirement and legal reviewer revocation
after retrieval, actual blocking of both withdrawals until the caller commits,
source withdrawal and membership/lease loss across real provenance lock waits,
and changed exact traces. These changes do not alter approval rules or grant
new database permissions. Audit remains IDs and hashes only.

The same final guard holds SHARE on the actual approved risk-policy row and
revalidates its current repository value after that wait. Retirement/activation
updates cannot cross source acceptance. A real blocking regression proves that
policy retirement waits for transaction completion; no rule or threshold changes.
