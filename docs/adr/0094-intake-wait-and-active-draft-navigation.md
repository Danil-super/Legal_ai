# ADR0094: clear intake waiting and explicit draft switching

Date: 2026-10-20. Status: implemented locally; release is separate.

## Evidence

The owner reported an ambiguous pending menu, a stopped/resume menu and a
repeated narrative question. Read-only production metadata establishes that the
initial operation was cancelled about 6.5 seconds after reservation, without a
preceding failure. Its draft retained the narrative. Subsequent EVENT activity
belonged to a different, older draft without a narrative. No patient text was
read or copied. This is not evidence of a general source-loss defect.

Independent actual-PTB reproduction proves that the initial manual control
requires an unnecessary second resume click. Control tests establish that the
saved source survives manual continuation, RAM loss and model failure, and that
generic resume skips an already populated EVENT checkpoint.

## Decision

- In simplified quick and guided intake, pending means wait. State explicitly
  that no click is needed. Only explicit cancellation/menu exit is shown.
- Retain historical manual controls, but execute continuation inside the
  ConversationHandler and immediately show the first missing question, after
  operation fencing and reauthorization. Do not simulate user confirmation.
- An initial failure already present in the start response is delivered through
  the existing actor-serial automatic resume path, just like a later failure.
- Historical draft/manual buttons cannot silently replace another active draft.
  Changing drafts remains explicit via /menu and the saved-draft list. This is
  navigation protection, not an authorization grant; Core remains authoritative.
- Both entries retain one complete factual-summary confirmation before case
  submission and automatic analysis. Never ask for an already saved narrative
  on generic continuation; explicit Back/edit can intentionally reopen it.

No schema, API, model, effort, risk threshold, clinical access, normative approval
or legal-evidence behavior changes. No raw texts in tests or audit evidence.

## Verification and limits

Regression tests cover pending copy/controls, legacy manual continuation,
asynchronous and immediate failure, source preservation after restart, guided
checkpoint recovery and stale cross-draft selectors. Existing report/submission
tests remain mandatory. These checks do not prove every provider response or
Telegram network failure succeeds, and do not establish universal legal quality.

Rollback restores the previous Gateway code; draft storage and operation
contracts are unchanged and remain readable. Production has not changed merely
because these local tests pass.
