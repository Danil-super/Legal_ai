# ADR 0113: distinguish urgent routing from AI plan completion

Date: 2026-11-05. Status: accepted within the authorized incident repair.

## Incident

An urgent V2 case can be routed before a date is known. The separate lawyer-plan
lookup then correctly refuses to select legal revisions (`ANALYSIS_DATE_UNCERTAIN`,
HTTP 422). Agent used to swallow that reason, and the worker called the retained
HUMAN_REVIEW routing receipt successful. Repeated submissions also created new
Telegram placeholders while durable delivery still targeted the first message.

## Contract

The immutable HUMAN_REVIEW receipt continues to prove routing, never plan completion.
Optional `lawyerDraftAvailable` and allowlisted `lawyerDraftErrorCode` fields on the
terminal V2 job envelope describe the separate plan. They are invalid for READY,
LIMITED, NEEDS_FACTS, nonterminal states, or contradictory available/error pairs.
Existing receipts and content hashes do not change; absent fields preserve old wire.

Agent returns the retained routing receipt plus a metadata-only private
`X-Lawyer-Draft-Error` header when the optional plan cannot finish. The worker persists
that bounded code in the existing job error column without erasing the routing
receipt. Unknown upstream errors collapse to LAWYER_DRAFT_UNAVAILABLE; raw upstream
messages never cross this diagnostic boundary. Status reads derive saved-plan
existence and, for old missing-plan attempts, the deterministic date gate.

When no plan exists and the current confirmed date is uncertain, the original case
creator may open an explicitly confirmed, prefilled correction draft bound to the
latest retained HUMAN_REVIEW operation/hash. Only the date is reopened. The existing
immutable-source correction workflow creates a new case after confirmation; it does
not rewrite the original case, transfer attachments, change its risk or reopen a
resolved escalation. Foreign actors, noncreators, exact-date cases, existing plans,
stale receipts, withdrawn access and expired content remain denied. Unknown dates
are never replaced with today's date merely to select legal evidence.

On explicit retry or a duplicate active submission, the existing job retains its
identity and lease but delivery targets the latest requesting private-chat message.
Notifier ACKs include `messageId`; after the actual row lock an ACK for a replaced
placeholder cannot mark the new one delivered. Legacy bodyless ACKs remain accepted
for compatibility. Terminal results are delivered directly on duplicate submission.

## Invariants and verification

No new table, role, source, model, provider, risk-policy version, timeout, or legal
approval. HIGH/CRITICAL routing and lawyer notification remain immediate. Plan
availability means an internal draft exists, not human approval or legal certification.
Source verification, date applicability and assigned-lawyer review remain mandatory.

Restore the already authorized lawyer-as-case-creator scenario: a CLINIC_LAWYER
can read the unapproved AI draft of their own case before claiming its escalation.
They still must claim it before editing/approving. Other unassigned lawyers cannot
use this creator exception; CLINIC_ADMIN still sees only human-approved current
versions. Tenant, membership, entitlement, retention and provenance checks remain.

Regression tests cover actual Agent/Core/PG boundaries with a lawyer creator and
unknown date, metadata-only failure propagation, creator-only recovery/replay,
unchanged original urgent routing, successful draft replay/review, latest-message
delivery and stale ACK fencing. External model calls in deterministic PG tests use
synthetic responses; real-provider probes are a separate check.
