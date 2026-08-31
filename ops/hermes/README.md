# Pinned Hermes runtime for legal analysis

This directory contains the only supported Hermes deployment posture for the legal-analysis slice.
It is intentionally separate from the default `docker-compose.yml`: intake-only mode must keep
working without Hermes or any LLM provider.

## Why the stock Hermes API server is prohibited

ADR-0013 pins Hermes Agent `0.20.6` to commit
`5fc308a70719a83cccdbba4c0e39c23f5a8239d5`. The pinned upstream tests show that the default
`hermes-api-server` toolset includes terminal/process, file mutation, web/browser, code execution,
delegation, memory and other tools. Dental Legal AI must never expose those capabilities to a legal
reasoning profile.

`legal-profile.config.yaml` requests an explicit empty `api_server` toolset and disables persistent
memory/profile features. That static file is **not sufficient by itself**. `assert_tool_free.py`
loads the actual pinned Hermes builtin/plugin registries and the final model-tool schema and exits
non-zero unless both the resolved toolset list and tool schema are empty.

## Build the exact upstream commit

Do not use `nousresearch/hermes-agent:latest` or another floating tag.

```bash
sh ops/hermes/build-pinned-image.sh
```

The script fetches exactly the commit from ADR-0013, checks the resulting HEAD and builds:

```text
dental-legal-hermes:5fc308a70719a83cccdbba4c0e39c23f5a8239d5
```

The upstream Dockerfile/lockfiles are therefore taken from the reviewed commit. A future Hermes
upgrade requires updating ADR-0013, the build script and compatibility/security tests together.

## Provider configuration

The overlay currently uses Hermes' custom OpenAI-compatible provider path. Keep all values in local
`.env`/secret storage; never commit them.

```text
HERMES_LLM_BASE_URL=https://your-reviewed-provider.example/v1
HERMES_LLM_API_KEY=...
HERMES_RESEARCHER_LLM_MODEL=...
HERMES_REVIEWER_LLM_MODEL=...
HERMES_RESEARCHER_API_KEY=<independent random API-server key>
HERMES_REVIEWER_API_KEY=<different independent random API-server key>
AGENT_INTERNAL_KEY=<random secret, at least 32 characters>
AGENT_ORCHESTRATOR_URL=http://agent-orchestrator:8010
```

The underlying LLM provider may be the same for both passes, but the Hermes origins and writable
profile state are separate. If the provider requires a non-OpenAI wire format, do not improvise in
this overlay; add a reviewed provider-specific configuration change instead.

## Start the isolated analysis stack

```bash
docker compose \
  -f docker-compose.yml \
  -f ops/hermes/docker-compose.hermes.yml \
  --profile analysis \
  up -d --build
```

Neither Hermes service publishes a host port. `agent-orchestrator` reaches researcher and reviewer
on the internal backend network; Hermes also joins `edge` only so it can reach the configured model
provider. The two services use separate named volumes and separate API-server keys.

A Hermes container whose resolved agent schema contains even one model tool exits during preflight
instead of starting its API server. This includes tools introduced by enabled plugins.

## Verification before a pilot

At minimum verify all of the following on the actual deployment host:

1. `docker image inspect` shows the local pinned image tag built by the helper script.
2. Both Hermes container logs contain a successful zero-tool preflight result with empty
   `enabledToolsets` and `toolSchemas`.
3. Neither Hermes container has a published host port.
4. Researcher and reviewer use distinct container origins and distinct API-server keys.
5. The configured LLM provider has passed the project's privacy/data-processing review.
6. A synthetic case can complete researcher → reviewer → Legal Core verifier without giving Hermes
   any legal-source, risk-policy, tenant-write or patient-send capability.

The repository CI validates the overlay's Compose syntax, but deliberately does not build the large
third-party Hermes image or call a paid LLM provider. Those are deployment smoke tests, not unit CI.
