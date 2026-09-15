# ADR-0031: Persist evidence abstention as a blocked analysis

## Status

Accepted

## Context

The research prompt requires abstention when the retrieved sources cannot support a claim.
The LLM and REST contracts previously required at least one claim, turning a correct abstention
into a provider error before Legal Core could record its existing fail-closed decision.

## Decision

Allow empty claim and review arrays in analysis submissions. An empty research proposal skips
semantic review and discards its unverified draft and recommendations. Legal Core keeps the
existing verifier/risk behavior: empty claims cannot pass verification, the result is
`ANALYSIS_BLOCKED` with unavailable risk and no patient draft. Reviews may only reference
submitted claim IDs; missing reviews remain blocked by the verifier.

This is a backward-compatible contract extension. It does not change the risk policy,
approval lifecycle, patient-draft policy, or authorization boundary.
