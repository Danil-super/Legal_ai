# ADR 0112: retain verified preliminary legal assessment in lawyer plans

Date: 2026-11-02

Status: accepted for implementation

## Problem

The model already proposes LEGAL and ACTION claims, and Core verifies both
against current approved legal fragments and confirmed facts. The internal
lawyer plan retained only ACTION text. The lawyer therefore saw a factual
summary and steps but lost the model's verified legal reasoning.

## Decision

Retain verified LEGAL claim text in `InternalWorkProduct.legalAssessment`, at
most eight nonblank de-identified items of 600 characters each. The same
all-claims verification gate remains mandatory: an unsupported or unreviewed
LEGAL claim prevents the entire draft from being published. References and
source freshness checks remain unchanged; historical scenarios do not become
legal evidence.

Render the assessment before recommended actions. The assigned lawyer can
revise assessment or actions separately using explicit numbered input, a
preview and confirmation. A revision creates a new HUMAN_REVISED version;
approval remains a separate explicit operation. The Gateway verifies that the
returned receipt changes only the displayed field and retains exact source,
legal references and previous-version binding. Core continues checking current
assignment, tenant, source freshness, expected version/hash and idempotency.

The new field defaults to an empty list and is omitted when empty. Existing
stored JSON and its canonical content hash therefore remain identical, without
a migration or backfill. Existing plans without an assessment remain readable.
New nonempty assessments participate in the immutable hash and privacy checks.
No risk policy, model/provider, entitlement or patient-delivery rules change.

## Verification

Tests reproduce the missing field/display and missing receipt comparison,
then cover old JSON/hash compatibility, bounded privacy validation, explicit
revision, Telegram preview/save, verified persistence, unsupported LEGAL
rejection and append-only human revision/approval. PostgreSQL integration tests
must run through the disposable project harness before release.
