# Source-bound internal work-product operations

Approved AIF07–10, 27 October 2026. This contract does not itself install a producer
or grant access. Normative approval, risk policy, canonical reports and automatic
patient delivery remain separate and unchanged.

The internal Agent submits expectedSource plus bounded claims/semanticReviews,
not a finished authoritative report, storage ID, version, human approval or patient
command. Core repeats the active creator/job lease and audited HIGH/CRITICAL context
checks after model I/O, verifies claims against the newly retrieved approved dated
evidence and chooses all immutable payload/author/version metadata itself.
Unsupported claims never become a published plan by dropping their dependencies.
No verified action means an explicit unavailable draft, not a fake useful report.

The public lawyer workspace reads the latest work product by escalation. Owners
and the assigned active lawyer may inspect the draft; its clinic creator may read
only a current HUMAN_APPROVED version. Another clinic or unassigned lawyer gains
no new work-product permission. Source freshness and content retention remain gates.

View: versionId, reviewState, origin, isCurrent, contentSha256, immutable content and
bounded citations. Payload status stays UNAPPROVED; only the separate Core-owned
human-review metadata records HUMAN_REVISED/HUMAN_APPROVED. A human review is not a
model verdict or approval of a normative version. Display those distinctions plainly.

Only the assigned owner/lawyer can POST an explicit review, bound to the displayed
version ID/hash and an idempotency key. APPROVE accepts no hidden text changes.
REVISE contains explicit bounded summary/actions/uncertainties and creates a new
human version without approving it. Human edits are attributable specialist text,
not newly model-verified claims. Review never changes facts, risk, case assignment,
normative approval or patient delivery; existing discussion supports targeted questions.
Retain the AI original and every human version, and never let a stale AI completion
replace human work. Replays reauthorize and check retention/source bindings.

Implemented routes, all default-off behind AI_FIRST_LAWYER_DRAFT_ENABLED:

- Internal POST `/v1/cases/{case_id}/lawyer-work-products` (Agent key and live v2 lease).
- Internal POST `/v1/cases/{case_id}/lawyer-work-products/lookup` (same creator/lease;
  returns a retained version or null so an HTTP retry need not rerun the model).
- GET `/v1/case-escalations/{escalation_id}/work-product` (current actor authorization).
- POST `/v1/case-escalations/{escalation_id}/work-product/reviews` (assigned human only).

Core owns the initial summary from confirmed facts, bounded verified ACTION steps,
deduplicated verified references and factual gaps. It rejects the entire proposal
when claims fail verification or no verified action exists. It cannot turn an empty
model answer into a useful-looking plan. AI retries bind to their original source
and hash-only audited receipt; a new automatic completion cannot replace human work.
Human review appends an immediate child version and never changes the AI original.

Case is locked before accessing the version table, matching retention. Source
provenance and current policy stay locked through commit, and actor, wall-clock
entitlement/retention, lease and operator flags are checked after actual persistence
or replay waits. Stale source/last-version failures cannot be approved. Receipts
contain only version IDs; retained content lives solely in the case-retained table.

Real HTTP/PostgreSQL tests cover submission/replay, failed semantic/structural gates,
role/tenant/assignment, human revision/approval, stale facts, revocation/safe-stop and
wall-clock lease expiry after flush, and a real writer blocked behind Case while
the actual retention function completes without a version-table deadlock.
Independent review, combined UI/Agent verification, full gates and a separate
production release decision remain; local tests do not establish universal legal quality.

Acceptance reuses the private loader's already fenced AnalysisState. The public
human review retrieves once before the final provenance guard, then reuses those
citations when appending its child version. This avoids a second optional embedding
request while source locks are held. Actual HTTP/PostgreSQL regression tests assert
one retrieval per write, alongside the existing post-flush authorization checks.
Core/worker accept true|1; the common Compose/Telegram deployment flag uses0|1,
defaults0, and is passed to all three services. No existing model/profile changes.
