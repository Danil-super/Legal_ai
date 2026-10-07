# ADR 0066: independently confirmed factual safety screening

Status: candidate implementation; human LEGAL_EDITOR activation remains mandatory.

The existing guided intake collects a narrative, not explicit negatives for legacy
legal-risk signals. `NO_KNOWN_INFORMATION` is not `NO`, and a generic uploaded
document is not a formal claim. Inferring either would incorrectly clear cases.

Add an independently confirmed `FACTUAL_SAFETY_SCREENING` JSON fact with exact
`schemaVersion: factual-safety-intake.v1`. Seven bounded YES/NO/UNKNOWN questions
ask about reported deterioration, hospitalization, representative contact, written
requirements, court/authority documents, mentioned authority referral, and requested
money. They do not ask the clinic to qualify harm, a legal claim, or a deadline.
Money uses exact integer kopecks/RUB; absent requested money is `NOT_REQUESTED`,
unknown amounts remain `UNKNOWN`. Existing intake facts and their history are kept.

This changes risk interpretation, so it is not enabled merely by adding the fact.
The candidate v3 payload must explicitly include the hash-covered capability
`factualSafetyIntakeVersion: factual-safety-intake.v1`, with a separate human
`factual_safety_intake_reviewed` attestation. Historical v1/v2 and the original
four-key v3 payload keep their behavior. Immutable existing versions cannot be
rewritten. Direct v1→v3 and matching LEGAL_EDITOR events remain required.

With this capability, complete independent answers can permit LOW/MEDIUM without
creating fictitious legacy NO facts. Explicit hospitalization or court/authority
documents are CRITICAL; reported deterioration, representative contact, written
requirements, or a requested amount >= 50,000 RUB are conservatively HIGH. Written
requirements are reported facts, not automatic legal qualification as a formal
claim. A positive money request below that boundary or mentioned authority referral
is MEDIUM. Unknown, malformed or conflicting prerequisites cannot clear the case.
An explicit urgent positive still routes to a lawyer before retrieval/model work.

All legal recommendations continue to require APPROVED, date-applicable retrieved
evidence and deterministic Legal Core validation. This adds no trusted source,
external provider, storage table, personal-data category or patient auto-send.

Tests cover exact JSON validation, monetary boundaries, all risk levels, unknowns,
conflicts, candidate hashes, old-version noninterference, durable UI transitions,
and the analysis worker/report path. Human review remains a deployment prerequisite.
