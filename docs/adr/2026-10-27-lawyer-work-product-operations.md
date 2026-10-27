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

Routes to implement with authz, source/fencing, lost-response and concurrency tests:

- Internal POST `/v1/cases/{case_id}/lawyer-work-products` (Agent key and live v2 lease).
- GET `/v1/case-escalations/{escalation_id}/work-product` (current actor authorization).
- POST `/v1/case-escalations/{escalation_id}/work-product/reviews` (assigned human only).

This types-first increment has no endpoints, writer or review side effects. Runtime
implementation, independent review, full gates and a separate release decision remain.
