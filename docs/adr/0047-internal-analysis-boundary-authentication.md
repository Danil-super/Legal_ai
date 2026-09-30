# ADR-0047: Authenticate the Legal Core analysis boundary

- **Status:** Accepted
- **Date:** 2026-09-30

## Context

The Agent Orchestrator reads a case analysis context from Legal Core and submits
model-proposed claims back to it. Tenant-scoped Telegram identity alone is not
an inter-service credential: any backend caller able to supply an ID could
otherwise request sensitive case context or attempt a submission.

## Decision

Both `/analysis-context` and `/analysis-submissions` require the shared
`X-Agent-Internal-Key` before any actor or case lookup. Legal Core fails closed
when `AGENT_INTERNAL_KEY` is absent or shorter than 32 characters. The
Orchestrator stores the same key only in its runtime configuration and attaches
it to every request. Existing per-request Telegram identity, idempotency and
analysis-job lease checks remain mandatory; the shared key does not replace
tenant authorization.

## Consequences

Direct callers receive the standard `403 INTERNAL_ACCESS_REQUIRED` error. The
analysis profile must configure the identical secret for Legal Core and Agent
Orchestrator. Tests cover missing, incorrect and correct-key flows. No key,
patient content or legal evidence is written to logs.

## Alternatives considered

- Rely on the private Docker network: rejected; network placement is not caller
  authentication and future operational changes could widen access.
- Use only Telegram identity: rejected; it authenticates an actor, not the
  trusted service allowed to request model-analysis material.
- Let the gateway call the protected endpoints: rejected; only the
  Orchestrator needs this internal analysis contract.
