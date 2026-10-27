# Optional case history and clinic actions in guided v2 intake

## Decision

The owner requested narrative-first case creation with only necessary follow-up
questions. `CONFLICT_STAGE` and `CLINIC_ACTIONS` are contextual facts, not risk-v4
triggers. Core readiness no longer requires these two facts for every case.
The existing v2 submission shape already supports their omission. No schema,
policy version, legal approval or verifier dependency rule changes.

Absent context stays absent. The Gateway must not emit a `USER_STATEMENT` with
`UNKNOWN`, `FIRST`, `NOTHING_YET` or a negative answer the user never supplied.
Explicitly supplied values remain available to retrieval, summary and editing.
The summary can display “не указано”; this is presentation, not a stored fact.
The two existing questions remain available for optional correction in the
summary, rather than blocking the automatic path to that summary.

Incoming communication/source, situation, narrative, date, health signals,
transport material status and treatment services retain their readiness gates.
Risk v4 retains its independent factual screening, missing/unknown-value gates,
10,000-ruble HIGH threshold and immediate escalation rules. Date applicability,
approved retrieval, semantic review and explicit final consent remain unchanged.

## Consequences

A recommendation that depends on prior clinic actions or conflict history still
requires those facts in `requiredFactKeys`. Core returns `INSUFFICIENT_FACTS` for
such a claim if context is absent, even when the semantic reviewer says supported.
A model must keep actions conditional where context is unavailable. This change
does not authorize invented facts or unsupported legal conclusions.

Tests reproduce the unwanted questions through the actual PTB dispatcher, prove
omission at the submission boundary, retain supplied context and optional editing,
and check LOW/5,000-ruble MEDIUM/10,000-ruble HIGH plus missing evidence, date,
screening and claim dependencies. HTTP/model/Telegram boundaries are faked in PTB
tests; they do not establish live-provider performance or legal release readiness.
