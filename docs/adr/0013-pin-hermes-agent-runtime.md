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

The pinned upstream API-server documentation confirms authenticated OpenAI-compatible
`/v1/chat/completions` and `/v1/responses` endpoints. The same pinned source also confirms that the
default `hermes-api-server` toolset includes terminal/process, file mutation, web, browser,
code-execution, delegation, memory and other capabilities. A stock API-server profile is therefore
**not** an acceptable Dental Legal AI deployment.

Pinned Hermes also supports per-platform `platform_toolsets`. Its resolver treats an explicit empty
`api_server` selection specially, including suppressing context-engine tools for that empty
selection. Because runtime/plugin discovery can still evolve within a release configuration, a
static YAML file alone is not treated as a sufficient security boundary.

## Decision

Production/staging integration MUST resolve Hermes from commit
`5fc308a70719a83cccdbba4c0e39c23f5a8239d5`. A tag name is retained only as human-readable
metadata. `main`, floating Docker tags and automatic `hermes update` are not accepted deployment
inputs for this service.

Hermes is used behind its authenticated API-server boundary. Dental Legal AI sends only a bounded,
locally pseudonymised case projection and approved evidence. Hermes does not receive source-approval,
entitlement, policy-write, patient-send, terminal, browser or arbitrary network capabilities for
this profile.

A legal Hermes profile MUST start from `ops/hermes/legal-profile.config.yaml` (or a reviewed
configuration with the same restrictions) and MUST execute `ops/hermes/assert_tool_free.py`
immediately before the gateway/API server starts. The preflight resolves Hermes' builtin and plugin
registries, asks the same platform resolver for `api_server` toolsets, builds the final model tool
schemas and exits non-zero unless **both** lists are empty. An operator cannot waive this check by
prompt instruction or by relying on a SOUL/system prompt.

Researcher and reviewer MUST use separate profile state and separate API credentials. They may use
the same underlying LLM provider if required, but the reviewer endpoint/profile identity must remain
independent from the researcher identity. Profile state, memory and user-profile features are
disabled in the legal baseline; Chat Completions requests remain stateless at the transport layer.

The provider/model itself is deliberately not hard-coded in this ADR. Enabling a provider requires a
separate data-processing/privacy review and secret configuration; the repository must not contain
provider API keys.

## Consequences

- upgrades require an explicit ADR/update commit and compatibility/security tests;
- Legal Core can continue operating in fail-closed mode while Hermes is unavailable;
- a Hermes outage or model/provider failure cannot mutate the legal corpus or risk policy;
- the default upstream Hermes API-server profile is prohibited for this project;
- a Hermes runtime whose resolved model tool schema is non-empty fails deployment preflight;
- separate researcher/reviewer profiles prevent shared writable memory/session state;
- provider selection remains replaceable and is not coupled to the Legal Core contracts.
