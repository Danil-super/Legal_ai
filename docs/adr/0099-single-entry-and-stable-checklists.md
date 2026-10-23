# ADR0099: One narrative-first entry and stable checklist cards

## Scope

Owner-requested UX correction, 23 October 2026. A new case has one visible entry:
«Создать кейс» → narrative → missing factual questions → one summary consent →
Legal Core evidence/risk gates → report or lawyer review. Existing guided drafts
and their callbacks remain resumable; they are not a second new-case menu choice.
The model interprets facts, not legal authority. No approval, risk, tenant, model,
provider, retention, patient-delivery or database contract is changed.

## Decision

Multi-select steps update only the existing Telegram reply markup, after a
matching Core persistence receipt. The question stays until «Продолжить →».
New checkboxes encode explicit add/remove intent, not an ambiguous toggle, so a
replayed click cannot undo the selection it originally requested. The current
question/field fence remains enforced. Empty Continue does not advance.

If Telegram cannot edit the card, keep the saved choices and provide a draft
reopen action. If Core cannot acknowledge persistence, do not mark the checkbox
or delete the old card. After Continue, existing cleanup requires saved progress
and a delivered next prompt before deleting the previous prompt. Files, original
narrative and final summary are not swept from history.

For failed interpretation recovery (ADR0098), retain an unsaved short answer;
only retire its known obsolete waiting message after a new prompt is delivered.
Any failure during recovery navigation falls back to draft reopen and menu,
never to claims that a lost write/cancellation succeeded.

## Verification and release

Actual PTB dispatcher tests cover stable message identity, persistence-before-edit,
duplicate add/remove, empty Continue, wrong-field click, save/receipt/edit faults,
single new-case entry and compatibility with old drafts. Independent review and
full disposable PostgreSQL/MinIO gates are required before release. Unit tests do
not establish legal correctness of every real-world case or provider availability.
