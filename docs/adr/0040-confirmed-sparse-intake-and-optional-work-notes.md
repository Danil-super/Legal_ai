# ADR 0040: Confirmed sparse intake and optional human work notes

Date: 2026-09-21
Status: Accepted

## Context and scope

The user supplied screenshots of a large claim-management application and requested
useful improvements without its long mandatory questionnaire. Adopt explicit review,
follow-up and closure, not that application's legal judgments, risk colours, patient
identifiers or mandatory clinical commission workflow. Screenshots do not establish
its implementation or legal correctness.

## Sparse intake

The existing local extractor remains a candidate generator, not a source of confirmed
facts. The production composition now shows the complete proposal and a per-field
correction menu before any draft write. Explicit confirmation persists all valid
candidate fields, including those after a missing earlier date. Unmentioned fields
are never NO. Hospitalization substring matches are deliberately not prefilled;
uncertain dates and detected contradictions are left for clarification. These are
conservative heuristics, not a comprehensive semantic contradiction detector.

The unchanged durable wizard state identifiers, REST schema, actor authorization,
revision checks, retention and final submission remain authoritative. Only input
callbacks of a newly built ConversationHandler are replaced at composition; no global
functions, filters, timeout, fallbacks or final submission are monkey-patched. After
each validated answer the next missing applicable field is selected; unknown dates
remain UNKNOWN and may still block legal analysis. Dependent fields are cleared when
a parent is corrected. The existing single response_deadline field is not expanded
or reinterpreted as a legal deadline in this increment.

Fresh review nonces reject old/cross-user confirmations. Create and save use separate
stable idempotency keys for ambiguous HTTP retries through the existing client/API.
No new draft schema, table, policy, LLM call or category of personal data is introduced.
The original extractor/legacy factory remains compatible for existing tests and
consumers, but the production entry point uses the new composition.

## Optional human case work

Reuse the existing authorized escalation discussion and closure endpoints. A current
assigned owner/lawyer may prepare a short work-plan note, specialist comment or closure
summary; none is mandatory for intake. Every write requires a preview confirmation and
fresh access/status checks. Role, tenant and closure authorization remain in Core.

A work plan records the author as the current responsible specialist and may include
an internal target date in free text. It is a history note, NOT a scheduler, new formal
assignment or a computed legal deadline. Specialist notes cannot overwrite automated
risk, approved evidence or canonical reports. Closure has an explicit second step;
payment, additional costs and prevention lessons are optional text, not automatically
summed financial metrics. Existing Core history stores the author/time and its existing
retention still applies. No patient message or patient-document upload is added.

Use the existing input-mode keys so menu navigation, switching cases and cancellation
clear pending content. Fresh nonces reject obsolete previews. Existing discussion
writes are not idempotent: consume a preview before POST, never auto-retry an uncertain
write, and ask the user to inspect history first. A network timeout is not reported as
successful save. Closed cases remain read-only through existing Core guards.

## Verification and limits

Tests exercise the real production PTB composition with network boundaries mocked:
non-contiguous fields, conditional questions, unknown dates, corrections/dependencies,
stale buttons, separate users, confirmation, reassignment, closed cases and navigation.
A disposable PostgreSQL test verifies sparse persistence, readback, idempotent replay,
foreign-tenant denial and inactive-subscription denial. Existing quality/security/image
and PostgreSQL/MinIO jobs remain required before deployment. Do not infer live model
quality, legal accuracy or user latency from these tests. No live clinical data is used.
