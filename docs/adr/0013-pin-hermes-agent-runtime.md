# ADR-0013: Pin Hermes Agent runtime to an immutable commit

## Status

Accepted for the legal-analysis integration slice on 2026-08-31.

## Context

Hermes Agent is an upstream agent runtime that changes rapidly. Dental Legal AI must not inherit
behavioural or tool-permission changes from upstream `main` without a compatibility/security test.
The legal source of truth, risk policy and tenant identity remain owned by Legal Core.

The current stable upstream release reviewed for this slice is:

- release: `v2026.8.27` / Hermes Agent `0.20.6`;
- annotated tag object: `fcebd62163497e77e5de00d26d2ed86cb4ef8761`;
- immutable commit: `5fc308a70719a83cccdbba4c0e39c23f5a8239d5`.

## Decision

Production/staging integration MUST resolve Hermes from commit
`5fc308a70719a83cccdbba4c0e39c23f5a8239d5`. A tag name is retained only as human-readable
metadata. `main`, floating Docker tags and automatic `hermes update` are not accepted deployment
inputs for this service.

Hermes is initially used behind its authenticated API-server boundary. The pinned release exposes
OpenAI-compatible `/v1/chat/completions` and `/v1/responses` endpoints. Dental Legal AI sends only
a bounded, locally pseudonymised case projection and approved evidence. Hermes does not receive
source-approval, entitlement, policy-write, patient-send, terminal, browser or arbitrary network
capabilities for this profile.

## Consequences

- upgrades require an explicit ADR/update commit and compatibility tests;
- Legal Core can continue operating in fail-closed mode while Hermes is unavailable;
- a Hermes outage or model/provider failure cannot mutate the legal corpus or risk policy;
- the selected Hermes profile must be configured with a dedicated API key and least-privilege
  toolset before production enablement.
