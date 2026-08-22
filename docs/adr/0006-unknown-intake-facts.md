# ADR-0006: Preserve unknown intake facts explicitly

## Status

Accepted

## Date

2026-08-22

## Context

An administrator can legitimately not know a date or the answer to a harm, claim,
hospitalisation, representative or regulator signal. Coercing this state into `false`, omitting
the fact, or inventing a date changes the evidentiary record and can hide a follow-up question.

The original `dental-case-intake.v1` implementation accepted only Boolean JSON values and dates
with `EXACT` or `APPROXIMATE` precision. That did not meet the fixed intake specification.

## Decision

`BOOLEAN` facts may use the established legacy shape `{ "boolean": true | false }` or the
additive tri-state shape `{ "state": "YES" | "NO" | "UNKNOWN" }`. New Telegram flows emit the
tri-state shape. Legal Core normalizes both shapes to their respective domain values without
converting `UNKNOWN` to `NO`.

`DATE` facts support `{ "date": null, "precision": "UNKNOWN" }`. A date is forbidden when its
precision is `UNKNOWN`; `EXACT` and `APPROXIMATE` still require a valid ISO date.

This is additive within `dental-case-intake.v1` so already stored Boolean facts and previously
issued reports remain readable. Facts remain append-only; a correction is a newer revision, not
an in-place mutation.

## Consequences

- Intake and reports preserve uncertainty for human review and later evidence checks.
- Conditional missing-fact rules must treat explicit unknown states deliberately rather than as
  absent or negative evidence.
- No risk score, legal conclusion or patient response is enabled by this representation change.
