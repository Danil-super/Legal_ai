# ADR-0068: Ground Hermes JSON instructions in strict contracts and exact citation IDs

## Status

Accepted

## Context

On 2026-10-07, after CI and deployment completed for `3876ec9`, two read-only
fictional QA projections reached the configured researcher through the existing
30-second Hermes client. Both returned JSON but failed the research contract at
`evidenceFragmentIds` with `uuid_parsing`, after 22.79 and 20.14 seconds. No reviewer
call followed. Closed-label diagnostics showed one claim, only expected top-level
keys and a known fact dependency; no model body or citation value was recorded.
The second citation was classified as an invalid UUID, not the literal example
token or the fixture's structural path. Copying a particular placeholder is not
established as the cause of the live response.

The instructions nevertheless contained pseudo-JSON alternatives such as
`"LEGAL" | "ACTION"`, dummy citation tokens, and no machine-readable exact citation
choices. This leaves a prompt/contract gap even while the strict validator correctly
fails closed. Successful small JSON pings do not prove two-pass legal reasoning.
The fictional evidence was process-local QA data, not a production approved source;
the probe created no case, report, approval or database record.

## Decision

- Remove pseudo-JSON response templates and dummy citation examples from both
  system instructions. Require a raw JSON object and explain exact ID copying.
- Derive the displayed JSON Schema from `ClaimProposalBatch` and
  `SemanticReviewBatch`, with aliases and all existing contract constraints.
  The research schema includes the complete `FactKey` enum from the actual contract.
- Add an enum of this projection's exact legal evidence UUIDs to research citation
  items. Clinic-document context is not included in that allowlist.
- Add exact proposal claim IDs and the union of cited evidence UUIDs to the review
  schema. A per-claim subset check remains mandatory; union membership alone does
  not authorize citing a fragment belonging to another claim.
- Keep schemas inside the existing bounded system prompt, including at maximum
  contract capacities. Do not add a provider, change models or timeouts, enable tools,
  retry indefinitely, repair UUIDs, select a substitute citation or weaken validation.

## Consequences

- A deterministic synthetic citation-template fixture reproduces the old invalid
  citation boundary and succeeds only when the schema supplies an exact actual ID.
  It tests the prompt contract; it is not proof of a stochastic model's reliability
  or proof that the live model copied that particular old example.
- Schema choices are instructions, not provider-side constrained decoding.
  Malformed responses, invented UUIDs, extra fields, unknown/duplicate fact keys,
  unapproved references and cross-claim review references remain rejected by the
  existing validators. No model response becomes legally verified by this change.
- Research abstention still suppresses unverified recommendations and the draft.
  Legal Core remains the source of truth and performs final evidence/date/risk gates.
- Unit tests do not call providers. Independent review and CI precede deployment;
  a bounded fictional researcher/reviewer live check is still required after review.
- This fixes a demonstrable prompt/schema gap, not the separate absence of approved
  production law or policy readiness. Actual case recommendations stay gated on
  applicable approved evidence; this ADR does not approve documents or activate policy.
