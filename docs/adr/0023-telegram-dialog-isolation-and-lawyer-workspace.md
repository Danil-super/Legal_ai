# ADR-0023: Isolate Telegram input modes and expose the lawyer case lifecycle

- **Status:** Accepted
- **Date:** 2026-09-12

## Context

The discussion text handler ran in an earlier PTB handler group than the case wizard. Its
`user_data` pointer survived opening a new draft; a new case fact could therefore be posted to
the previous escalation instead of the wizard. Merely checking callbacks independently did
not reveal this: reproducing `Application.process_update` did.

Lawyers also need the complete case facts, verified report, latest discussion, assignment and
an explicit conclusion. A single truncated Telegram message cannot carry a long discussion.

## Decision

An early, read-only-navigation boundary clears pending free-text modes before consumers run.
Navigation out of a discussion clears both its target and pending resolution. Case start and
resume additionally clear discussion state themselves. The existing sequential
`ConversationHandler` remains authoritative for the active wizard; an old discussion/action
button cannot override it. The user returns to the main menu first, preserving the durable
draft. No private PTB conversation internals are mutated and no authentication rule is changed.

The lawyer workspace reads tenant-authorized detail from Legal Core. It displays risk reasons,
status, responsible lawyer and a facts preview, with a full facts/report attachment and the
canonical PDF when available. Assignment and resolution go through Legal Core; the UI is not
an authorization boundary. A separate resolution prompt explicitly names the case, requires a
conclusion, and routes the next message to resolution instead of ordinary discussion. Cancel or
navigation removes this pending intent. Resolved cases remain readable in a separate queue.

Queue and discussion history use backend cursors. History is fetched five messages at a time,
in chronological order, and split into bounded Telegram messages without truncating message
bodies. Older-history callbacks encode two UUIDs as base64url so the case and cursor are
self-contained within Telegram's 64-byte limit; they are pointers, not credentials. Every
request still authorizes the actor and tenant at Legal Core.

## Alternatives considered

- Enabling concurrent updates: would not repair overlapping consumers, and breaks the current
  sequential conversation assumption. Background analysis is a separate change.
- Clearing only the wizard's dictionary: leaves PTB's conversation state live, causing a second
  mismatch. We preserve the wizard until its normal menu/cancel transition.
- Saving only a current-case cursor in memory: old buttons could silently navigate a different
  case after a mode switch. Self-contained callbacks avoid this ambiguity.
- Truncating the latest history page: loses responses. We split complete messages instead.

## Verification

`test_dialog_isolation.py` processes real PTB updates with only Telegram and Legal Core network
boundaries faked. It covers new wizard, resumed draft, quick intake, main menu, quick-to-wizard,
an old discussion button during a wizard, and claim → message → explicit conclusion → resolved
history. `test_escalation_workspace.py` proves complete long-message rendering, bounded cursors,
assignment-aware controls and separate resolution routing. Backend tenant/concurrency tests
cover the server authorization and state transitions; these frontend tests do not replace them.
