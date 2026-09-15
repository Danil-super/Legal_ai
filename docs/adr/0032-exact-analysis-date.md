# ADR-0032: Fail closed when the applicable case date is uncertain

## Status

Accepted

## Context

Legal retrieval uses one date to resolve half-open validity intervals. Previously, unknown case
dates silently became today's date and approximate dates were treated as exact. Either behavior
could select the wrong legal revision, including at the 2026-09-01 decree transition.

## Decision

Preserve the existing date priority: claim, incident, service. Skip absent/unknown/invalid dates,
but require the first known valid date to have `EXACT` precision. If that date is `APPROXIMATE`,
do not replace it with a different lower-priority event date. Until the product has an explicit
uncertainty interval and can prove that a legal version applies throughout that interval, require
the operator to clarify the date.

When no exact applicable date can be selected, the analysis context/submission returns HTTP 422
with `ANALYSIS_DATE_UNCERTAIN` before corpus retrieval or external reasoning. The finalized intake
remains available and analysis stays blocked. Telegram explains that the date must be clarified.
There is no fallback to the current date and no change to risk or escalation policy.

This does not claim that a single selected date answers every temporal legal question. Separately
resolving service, incident and claim obligations remains a future modeling decision.
