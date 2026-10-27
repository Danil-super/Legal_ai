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
