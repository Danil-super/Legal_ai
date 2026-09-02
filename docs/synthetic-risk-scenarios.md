# Synthetic risk regression scenarios

`services/legal_core/corpus/synthetic_risk_scenarios/risk_scenarios.v1.json` contains 20
original, non-identifying inputs for the deterministic risk engine. The pack exercises `LOW`,
`MEDIUM`, `HIGH`, `CRITICAL` and fail-closed `UNAVAILABLE` outcomes, including P0 escalation
triggers.

It is marked `DEVELOPMENT_AND_REGRESSION_ONLY`, `NOT_A_LEGAL_SOURCE` and
`AUTHORED_SYNTHETIC_NO_EXTERNAL_CASE_TEXT`. It has no patient, clinician, clinic, author or source
identity; it must not be treated as a practical-case library, a recommendation source or training
data.

Each scenario contains only the bounded typed facts consumed by `risk_engine.py`, a frozen evidence
gate flag and the expected deterministic result. CI runs every scenario through the actual risk
engine. A future licensed practical-case library must use a separate reviewed persistence workflow
and cannot overwrite or silently promote this synthetic pack.

Before an immutable `risk-policy` version can be approved, Legal Core reruns the P0 scenarios
against the candidate threshold. A P0 mismatch stops approval before any database access; changing
the policy therefore requires an explicitly reviewed scenario-pack update as well.
