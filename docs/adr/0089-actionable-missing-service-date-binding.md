# ADR 0089: make a proven missing service-date binding actionable

Date: 2026-10-14. Bug-fix continuation within approved factual clarification scope.

## Reproduction

With the approved 55-second Hermes trial, a real fictional quick case requested
payment/date confirmation. Its confirmed successor completed researcher/reviewer
and Core PASSED/READY, cited two approved fragments and delivered a checksum-verified
PDF to the owner. A guided fictional case instead returned service-dependent claims
and no questions: a date in narrative was treated as known by the researcher, but
Core correctly required slot-bound confirmation and blocked SERVICE_DATE_BINDING_UNKNOWN.
The blocked result had no actionable question. This is not permission to infer dates.

## Decision

After rejecting invalid model requests and running the unchanged verifier, Core
may append only missing SERVICE_PROVIDED_DATE requests when an actual verified-run
claim has that exact block reason. Use existing treatment service slots and schema,
preserve existing question order, deduplicate, and cap the combined batch at three.
Every attempted date identity, including UNKNOWN/APPROXIMATE, is excluded; five
missing slots progress as 0–2 then 3–4 after confirmations. No inferred dates or
frozen-fact mutations. No fallback for unrelated reasons, empty claims, legacy or
non-treatment scope. Malformed existing requests still fail before fallback.

The research prompt aligns narrative-date versus confirmed slot binding and forbids
repeating attempted UNKNOWN/APPROXIMATE questions. It must not invent answer-bundle
dependencies: with no answers those fail earlier as ANALYSIS_CLARIFICATION_ANSWERS_UNKNOWN,
which intentionally does not activate this fallback. Not every blocked analysis is
fixable with a date question, and answering never guarantees readiness.

No wire/storage schema, tenant/auth, sources, approvals, risk policy/escalation,
retrieval anchor, provider/model/reasoning, timeout or patient-send changes. Existing
report-bound creator-only recovery handles these same questions; old reports remain
immutable. A compatible prior FC revision may read the new reports on rollback.

## Verification

RED: actual submission tests reproduced two date-gap failures while 14 prior tests
passed; helper contract tests initially failed for the absent implementation.
Required GREEN: bounded scope/no mutation/no loop, original invalid-request/tenant
denial and idempotency, temporal/semantic/risk gates, full disposable PG/MinIO suite,
independent review and live guided recovery plus positive/negative smoke. Process-local
model success, healthy containers or a prior quick positive do not close this gate.
