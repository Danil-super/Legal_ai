# ADR0101: One model call for a factual answer and source-bound additions

27 October 2026, implementing approved AIF03/04. The original single-question
contract remains supported. A new optional `factualProposal` is omitted when absent,
preserving legacy serialized responses and proposal hashes.

The Agent may use the rich path only when Core supplies retained source segments
and an allowed target list. The currently supported source bundle contains the
current retained narrative plus the latest answer; previously staged values stay
in typed factual context. Uploaded text/files, actor IDs and tool permissions are
not added to model context. This does not claim recovery of all historic raw replies.

The same existing researcher/profile makes one call. The numeric-only monetary
shortcut still makes none. The current question can be corrected; additional
targets are fill-only. Existing UNKNOWN/negative values cannot be silently replaced.
Candidates reference exact source IDs, hashes and offsets, use existing closed
field/value contracts and retain date/service identities. Additional money needs
literal ruble currency and exact conversion. Related fields are validated as one
atomic batch, irrespective of candidate order.

Core independently repeats target/source/value checks before staging, under its
existing ownership, live lease, revision and audit fences. Additional facts remain
provisional until the final factual-summary confirmation. These structural and
literal checks are not proof of semantic truth; ambiguous or conflicting input
still requires a targeted clarification, never an inferred safe answer.

An unresolved current answer currently remains the existing recovery flow; it may
not hide additional candidates in a successful-looking result. No new normative
approval, risk/escalation rule, model/provider/profile or patient-send path is added.
This increment does not implement the lawyer work-product table/producer or legal
plan generation. Reader compatibility, PostgreSQL staging/authz tests and independent
review are required before release; local mock-model tests do not prove live speed
or legal quality.
