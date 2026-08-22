# ADR-0003: Use one canonical case report for Telegram and PDF

## Status

Accepted

## Date

2026-08-22

## Context

The administrator needs a short Telegram result while clinic management and lawyers need a
stable PDF. Independent templates would drift and could show different facts, legal sources or
risk states. The system must also distinguish a useful intake summary from an evidence-backed
legal report.

## Decision

Store each immutable report as `dental-case-report.v1` JSON plus a deterministic PDF rendered
from exactly that JSON. Both presentations expose the same report version, fact snapshot hash,
legal snapshot and correlation ID. Missing legal capability is represented explicitly as
`ANALYSIS_BLOCKED`/`NOT_AVAILABLE`; sections are not silently omitted or populated from model
memory.

Fact changes supersede prior reports. New reports receive a monotonically increasing version.
The Telegram gateway cannot construct legal content independently from Legal Core.

## Alternatives considered

### Separate Telegram and PDF models

Rejected because duplicated mapping logic creates inconsistent legally significant outputs.

### Generate a PDF directly from chat history

Rejected because chat history contains unstructured, potentially sensitive input and has no
stable provenance or snapshot boundary.

### Hide unavailable legal sections

Rejected because users could mistake an intake summary for a completed legal assessment.

## Consequences

- Renderers stay simple and testable against one schema.
- Historical reports remain auditable.
- A report can be generated before legal analysis, but its blocked state is unmistakable.
- Schema changes require a new version and contract tests.

