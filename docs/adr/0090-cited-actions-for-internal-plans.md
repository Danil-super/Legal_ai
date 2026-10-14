# ADR 0090: request source-grounded actions for the internal plan

Date: 2026-10-15. Scoped correction within the owner's requested source-grounded report.

## Context

PR98's live fictional guided and quick paths produced Core READY/PASSED reports;
UNKNOWN also produced a persisted safe block after a transient provider failure.
One positive quick report contained verified LEGAL conclusions and no ACTION claims.
Consequently the plan used its generic staff-review fallback, while model-side
internalRecommendations cannot be published as verified actions. The evidence
already supported concrete receipt/review steps; this is a producer contract gap.

## Decision

Explicitly ask the researcher to represent concrete, nonduplicative supported plan
steps as ACTION claims, with exact evidence IDs and honest fact dependencies.
No forced action/count when facts or evidence are insufficient. Preserve service
dependencies and exact-date gates: do not reclassify LEGAL as ACTION to bypass them.
The current schema cap is30 claims, not12; no cap, model/high, provider, request55,
stage/shared budgets, riskv4/10k, sources, approvals, roles/auth or storage changes.

Core still publishes only VERIFIED ACTION and never raw internalRecommendations.
Existing LEGAL claims/history are not rewritten as actions. Unknown/approximate
or historically inapplicable service dates remain blocked. All claims still must
verify; adding unsupported actions can block an otherwise valid analysis, so the
prompt asks only for essential supported steps rather than padding a plan.

## Verification and limits

RED: prompt contract test failed for the absent actionable-plan instruction.
Existing source/date/semantic gates are unchanged; new service-dependent ACTION
tests explicitly exercise unknown/approximate/earlier/exact dates and the publication
filter. These guard tests passing before the prompt change are not evidence of a
new verifier fix. Required acceptance: full regression and independent review,
then real Core-persisted plan with concrete VERIFIED actions and no generic fallback,
plus truthful UNKNOWN negative under unchanged budgets. Static prompt assertions,
HTTP200 or process-local reviews cannot prove model compliance or legal readiness.
