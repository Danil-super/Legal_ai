# ADR 0087: bounded factual clarification after analysis abstention

Date: 2026-10-14. Status: accepted within the owner-approved
`SPEC-analysis-factual-clarifications.md` and FC1–FC11 plan; not release approval.

## Context

The synthetic manual and quick paths can end in a safely blocked, empty-claim
analysis without concrete factual questions. The model must not invent a legal
classification, publish unverified advice, or silently infer missing facts.

## Decision

Share three allowlisted question IDs between Core and orchestration. Payment and
service-date questions require a strict integer service slot 0–4; response purpose
is global. At most three unique requests are accepted per batch. No question text,
tenant, free-form answer or commands are accepted. Answers use bounded factual
choices and canonical calendar dates with explicit precision; UNKNOWN remains
unknown, not false. At most eleven unique answers fit five existing services and
one global purpose. Only user-confirmed answers enter the existing JSON fact and
draft lifecycle. No new table, source, model, role or risk policy is introduced.

The report and submission contracts read an additive `clarificationRequests`
field. Empty defaults are excluded from serialization using the installed pinned
Pydantic 2.13.4 `Field(exclude_if=...)` mechanism, documented in
[Pydantic serialization](https://pydantic.dev/docs/validation/latest/concepts/serialization/#at-the-field-level).
Old payload serialization is unchanged. A READY report cannot contain pending
questions. Questions are neither legal claims nor evidence; no claims still means
BLOCKED, with unverified recommendations/drafts discarded and no reviewer call.

FC1 is a reader-only baseline. Record its verified immutable commit before FC2
enables input and before FC4 writes new report fields. After new fields/fact keys
exist, rollback requires that baseline or a later compatible reader, not the old
production revision which rejects unknown fields/keys. No historical report/PDF
or confirmed fact snapshot is rewritten to manufacture compatibility.

Subsequent tasks must validate model requests against the frozen Core facts and
tenant, bind recovery to the displayed source report and its hashes, and produce
a creator-only new draft. Original cases/files remain unchanged. Known answers
are not requested again; a confirmed UNKNOWN attempt must not trigger an infinite
loop. EVENT_DATE does not become SERVICE_PROVIDED_DATE. Earlier or unknown service
dates cannot be justified by later-only evidence. Urgent routing is unchanged.

## Verification and limits

Synthetic contract tests cover strict slots, extra fields, duplicates, overflow,
typed answers, invalid calendar dates and legacy read/serialization compatibility.
Input, orchestration, real PostgreSQL persistence, durable Telegram recovery,
temporal gates and presentation are separately verified in FC2–FC11. This ADR does
not claim those later stages are already implemented or legal readiness proven.
Independent review, full regression and a real Core-persisted fictional positive
answer plus clarification/negative smoke remain release gates.
