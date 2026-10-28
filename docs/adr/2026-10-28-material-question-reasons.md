# Bounded reasons for factual questions

The approved AIF05 contract asks for an internal reason for factual clarification,
without displaying legal/technical routing metadata to clinic staff. Core now
explains the already selected `AnswerQuestion` using the existing `MaterialQuestion`
contract. Incoming/health/screening questions can affect escalation; situation,
service, payment basis and dates can affect source applicability; the narrative,
response purpose and explicitly opened optional context guide action selection.

This classification does not select a question, establish its necessity, make
optional context mandatory, or reopen a known/explicitly UNKNOWN answer. Those
checks remain in the current draft, scope and attempt selection. Extraction-only
proposed service slots are rejected rather than promoted to user questions.
Forged model instances are reparsed against the existing closed question schema.

The existing `INTAKE_ANSWER_RESERVED` audit receives only the bounded reason code
and wizard state beside its existing revision/question hash. It contains no
answer/narrative, new facts, model prose, identifiers or legal conclusions.
Retries retain one reservation audit; old rows without this metadata remain readable.
Intent identity, question serialization/SHA, private/public wire, stored operations
and strict completion/proposal/replay audits are unchanged. No migration, model,
risk-policy or permission change is involved.

Tests prove applicability versus action selection, preserving unknowns and legacy
question hashes, rejecting extraction slots and forged questions, and real
PostgreSQL reservation/replay metadata across safety/date/optional-context paths.
The same original completion/read lifecycle still validates its separate immutable
proposal audit. No batching or recovery of unretained historic raw text is claimed.
