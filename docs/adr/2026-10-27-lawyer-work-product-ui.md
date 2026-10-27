# Readable internal plan review in the existing lawyer workspace

Owner-approved AIF09/10 clinical UI slice, 27 October 2026. Core remains the
authority for case access, current source/evidence, versions and review writes.
No Core, Agent, DB, risk, personal feature or patient-delivery behavior is added here.

The optional `AI_FIRST_LAWYER_DRAFT_ENABLED=1` gateway flag installs an internal-plan
subflow. It defaults off, leaves the default factory arguments/handlers intact and
adds no HTTP request when disabled. The composed clinical workspace shows one
«План решения» button only after the existing Core work-product GET authorizes a
valid case-bound version. An unavailable draft does not break the case workspace.

The plan presents the situation, numbered actions, short legal document/article
references and uncertainties. Human review status comes from the outer immutable
version metadata, never the content artifact's literal UNAPPROVED field. Old source
versions are marked outdated and have no edit/approve controls. No model names,
UUIDs, hashes, JSON, fake PDF or VERIFIED label is shown in plan text.

Only the currently assigned owner/lawyer in an open in-progress case can see review
controls, with current permissions checked again before the POST. Core independently
enforces these rights and stale-source checks. Read-only owner/creator visibility
comes from Core; the UI cannot grant it. Callback identifiers are opaque local
selection handles, never credentials.

Approval explicitly binds the displayed version ID and content SHA256 and sends no
edits. Editing changes only the ordered action list: one to eight numbered steps,
each up to 600 characters, without direct identifiers. A local preview precedes an
explicit REVISE write; facts, source bindings, summary, uncertainties and legal
references remain unchanged. Revision does not approve a plan or close a case.

Idempotency keys derive from actor/case and the complete immutable review request.
A lost POST reply retains the same explicit retry, payload and key. A second click
cannot write a second version or invalidate the new version's controls. A refusal,
changed assignment or stale version returns to the case; it is never reported as a
successful review. Core's typed receipt is checked against the displayed version
and intended decision before the new plan is shown.

A navigation fence clears local pending edits on menu, cancel or another flow.
The text handler runs before generic description/discussion consumers, only when
this plan is awaiting an edit and no other input mode is active. Edit text cannot
become a discussion message or replace a running case intake. The existing private
chat boundary remains enforced. Subflow screens have one back-to-case control.

Tests run the actual PTB composition with fake HTTP/Telegram boundaries, including
disabled mode, role/stale/assignment refusal, preview, duplicate/lost replies,
navigation/input isolation, malformed buttons, private chat and UTF-16 page bounds.
Minimal gateway packaging copies only the pure shared contract, with negative
missing-file coverage; importing the UI does not require ORM/Core service modules.
This is not a live-service or legal-quality acceptance test and does not enable a
production release. The personal-interface canary's clinical factory fingerprint
changes only for this reviewed opt-in clinical extension; personal routes stay inert.

Focused verification: 126 tests pass with no skips, including the exact-copy gateway
packaging and personal-boundary canary. Ruff passes; gateway mypy covers 54 source
files. Removing the assignment predicate makes the read-only regression fail;
restoring it passes. Both exact-worktree code graphs are updated. No live Telegram,
Core/Agent service, production migration or real-lawyer acceptance run is claimed.
