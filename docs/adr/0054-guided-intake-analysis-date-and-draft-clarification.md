# ADR-0054: Use the guided intake event date for time-dependent analysis

## Status

Accepted

## Context

ADR-0032 requires an exact date to select the applicable legal revision. Its
`CLAIM_DATE` → `INCIDENT_DATE` → `SERVICE_DATE` priority describes the legacy
intake. The guided v2 intake instead collects `EVENT_DATE` and deliberately does
not ask the clinic to classify a communication as a legal claim. The analysis
boundary nevertheless still read only legacy date fields. A complete v2 case
therefore failed with `ANALYSIS_DATE_UNCERTAIN` after finalization. A legacy
Telegram draft with all three dates unknown could also be finalized before that
failure became visible, leaving an immutable case that could not be corrected.

## Decision

- For `GUIDED_V2`, select only `EVENT_DATE` for the current single-date legal
  retrieval. It must have `EXACT` precision. Do not fall back to a legacy date,
  today, or a lower-priority event when this date is approximate or unknown.
- For legacy intake, retain the ADR-0032 priority unchanged: claim, incident,
  service. An approximate first known date still blocks analysis even when a
  lower-priority date is exact.
- Telegram asks for the missing precise date before its workflow submission.
  A legacy operator can choose which of the three dates they can substantiate;
  an approximate higher-priority date must itself be clarified. Until then the
  draft remains editable and can be saved. Stale v2 confirmation buttons also
  return to the date question.
- The Legal Core remains authoritative and continues to fail closed on uncertain
  analysis dates. Existing finalized cases are not reopened or mutated.

## Consequences

This resolves the guided v2 date mismatch without changing risk policy,
escalation, legal-source approval, or the prohibition on automatically sending
legal responses to patients. The selected date is still a single retrieval
anchor; obligations arising on different dates require a separate future model.
Already-finalized legacy cases with unknown dates are not repaired by this UX
change and need a new intake if analysis is desired.
