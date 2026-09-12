# Bot reliability remediation

Status: implementation; requested by owner on 2026-09-13.

## Acceptance contract

1. Lawyers, not software, approve legal versions. An approved, date-applicable verified copy
   is available through library, retrieval and analysis-context with unchanged integrity guards.
   Pending, corrupted, unauthorised and date-inapplicable artifacts remain excluded.
2. Analysis is a durable job, not an awaited Telegram update handler. An acknowledged job
   survives service restarts; duplicate clicks do not start parallel analysis for the same case.
   A bounded worker runs the existing researcher/reviewer/Legal Core verification chain.
   Status is user-readable; completion edits a durable Telegram status message. No automatic
   legal response is sent to a patient. The target is menu p95 <1s with five deliberately slow
   synthetic analyses in a controlled test, not a promise of zero Internet/LLM latency.
3. A new versioned early-triage policy creates HIGH/CRITICAL human-review records from known
   deterministic signals when intake is confirmed, independent of evidence/model availability.
   Missing evidence still blocks legal recommendations; LOW/MEDIUM is never guessed.
4. Starting/resuming a case, quick intake, menu or admin operation exits the previous input
   mode. Old buttons and text cannot cross-wire new facts into another case discussion.
5. Lawyers can open the actual escalation card with facts, risk reasons and available report,
   read chronologically paginated discussion, take responsibility, answer and resolve the case.
   Tenant/user/subscription permissions are checked by Legal Core for every operation;
   assignment/resolution are auditable and stale/conflicting operations fail safely.
6. Editor retains primary full-PDF access and can inspect/export the complete selected
   excerpts before attesting them. Navigation and confirmation are usable; no fabricated
   page numbers or automatic legal approval.

## Work ownership

- corpus_implementation: approved-copy view and editor review/export.
- lawyer_implementation: early triage version, escalation card/lifecycle/discussion backend.
- dialog_implementation: mode isolation and lawyer Telegram workspace.
- root: durable analysis jobs/worker/status delivery, integration, adversarial review,
  quality gates and rollout through the existing GitHub deployment workflow.

## Security and reliability boundaries

Telegram identities are not tenant IDs. Server actor resolution determines clinic context.
The queue holds references/operational metadata rather than duplicate medical facts or PDFs.
Worker cross-tenant discovery uses a bounded privileged database function, not a BYPASSRLS
runtime account. New internal notification endpoints require the existing strong internal
service key. Lease tokens fence stale workers. External model responses remain untrusted.
Retries preserve analysis idempotency; ambiguous provider timeouts must not be presented as
successful analysis or silently produce duplicate reports. No new external service is added.

## Verification gates

- Reproduction tests fail before fixes; focused tests pass after each slice.
- Fresh PostgreSQL migrations + full PostgreSQL/MinIO suite, Ruff, mypy, Compose and dependency audit.
- Cross-tenant/role/idempotency/concurrent-worker/expired-lease/restart/error tests.
- Integrated synthetic approved-copy → confirmed case → background analysis → verified report,
  and HIGH/CRITICAL → lawyer detail → discussion → resolution.
- Measured callback responsiveness while analysis is blocked by a controlled fake provider.
- Independent agent review reconciled by root; changes reviewed before GitHub deployment.
- Production checks never approve normative documents or publish real case content.

## Deferred recommendations, not silently implemented

Corpus edition supersession, expanded law coverage, access-management enhancements, full
case archive and global private-chat policy are separate follow-up recommendations unless
needed to satisfy the six explicit remediation items. The editor's initial approval is not
proof that every conceivable legal question is covered.
