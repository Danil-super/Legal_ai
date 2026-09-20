# ADR 0034: Read-only owner diagnostics for analysis prerequisites

Date: 2026-09-20
Status: Accepted

## Context

An intake PDF, container liveness and an approved document are different prerequisites,
not proof that a legal answer can be generated. Operators need to distinguish disabled
analysis, unreachable services, absent policy and empty approved corpus without making
paid model calls or reading patient data.

## Decision

Add GET /v1/analysis-diagnostics and the private-chat Telegram command /analysis_status.
Use the existing active membership and subscription resolver, then require CLINIC_OWNER
before any diagnostic reads or internal network probe. Return fixed enums, timestamps,
application version and an optional validated DENTAL_RELEASE_SHA only. The gateway
renders allowlisted labels and does not print raw error envelopes or configuration.

The corpus check reads at most one approved version effective today. It does not assert
case-date coverage. The policy check uses the existing approval-aware repository. The
last successful report timestamp is restricted to the requesting owner's clinic.
A bounded, non-redirecting GET of the configured orchestrator liveness endpoint yields
REACHABLE, not READY: provider execution and case coverage remain explicitly NOT_TESTED.
No API keys are sent to that probe. No analysis, embeddings, approvals or writes occur.

The diagnostics command is composed even in intake-only mode and remains behind the
existing shared-chat guard. It cannot repair a gateway that cannot start or connect to
Telegram. A malformed gateway startup configuration can still prevent command delivery.

DENTAL_RELEASE_SHA is optional provenance supplied by an operator/build configuration.
It is not guessed from current main. The existing deployment does not yet inject it;
missing or invalid values are displayed as unavailable rather than a fabricated SHA.

## Verification

Tests cover owner-only authorization, inactive access, clinic-scoped timestamps, approved
metadata, disabled/invalid configuration without network, redirects and network failures,
identity validation at the HTTP route and safe rendering. The existing COPY-set test
imports the newly composed real gateway without leaking source-tree dependencies.
Risk policy, all-or-nothing claim verification, tenant identity and patient sending are
unchanged. Full model readiness still requires a synthetic end-to-end analysis.
