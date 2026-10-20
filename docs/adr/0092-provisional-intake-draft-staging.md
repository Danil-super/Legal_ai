# ADR 0092: Provisional intake staging before a single final fabula confirmation

Date: 2026-10-20. Status: implementation contract.

## Context

The user requested a conversational intake: describe the situation, answer only
missing factual questions, confirm one final fabula, then obtain the result.
Per-field model interpretation confirmations add unnecessary intermediate menus.
Removing those buttons must not fabricate user acceptance of a model proposal.

## Decision

Add POST `/v1/intake-preparations/{operation_id}/draft-staging` and
`/v1/intake-answer-preparations/{operation_id}/draft-staging`. Both reuse the strict
`AcceptUnderstandingRequest` immutable revision/input/proposal selector and return
only `{operationId, stagedRevision, draft}`. This reuse is a binding contract,
not a claim that staging expresses user consent.

Core stages a current model proposal into the mutable, creator-owned draft. It
does not create a case, facts, finalization, report, model call or acceptance
receipt. The existing final-summary confirmation remains mandatory. The old
acceptance/status/history protocols and stored operation JSON are unchanged.
Old controls become stale after a staging revision; they never report ACCEPTED.

The transaction uses existing draft-first/operation-second ownership locks and
post-lock role/tenant/entitlement/retention checks. Initial suggestions use the
existing proposal/audit/source validation and conflict-preserving merge. Answer
proposals use the stored original answer, derived question, source spans and
existing value/application validators, never a Gateway-rendered preview.

An `INTAKE_PROPOSAL_STAGED` audit records only binding metadata and hashes,
including kind, draftId, source revision, stagedRevision=source+1, request hash,
output hash and requiresFinalConfirmation=true. It is not an INTAKE_*_ACCEPTED
event and contains no raw user text. No schema or migration is required.

Retries acquire the same locks and validate exactly one owned staging audit before
source freshness. The request must match the original selector and receipt;
the current draft must be at least the staged revision. At the staged revision,
the output hash must still match. A later legitimate edit is returned without
overwriting it or extending retention. Malformed, duplicate, conflicting or
unauthorized receipts fail closed. Proposal metadata and immutable proposal audit
are rechecked on every replay, without re-running the model.

## Alternatives and consequences

Calling old acceptance endpoints silently would fabricate explicit consent.
Applying Gateway previews would bypass stored-answer grounding and conflict checks.
Both alternatives are rejected. Provisional staging instead adds a narrow API
while preserving the legal boundary: only the user's final fabula confirmation
may submit the assembled facts to Legal Core for risk/retrieval/report processing.

Real PostgreSQL tests cover both endpoints, exact replay, concurrent retries,
later edits, stale/tampered proposals, corrupted receipts, live authorization and
the absence of case/fact/report/acceptance side effects. These tests do not prove
live model semantic quality, legal adequacy or Telegram delivery performance.
