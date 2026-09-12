# ADR 0030: Preserve typed service identifiers in provider privacy validation

Status: accepted for implementation, 2026-09-12.

## Evidence and decision

A real Core → durable worker → orchestrator integration test failed before any model call:
the direct-identifier regex treated numeric runs within randomly generated case/fragment UUIDs
as patient identifiers. Fixed-zero fixture UUIDs had hidden the defect.

The final pre-provider scan now excludes only the schema-typed `case_id` and
`evidence[*].fragment_id` fields from its validation copy. The actual provider projection retains
these references because evidence attribution requires them. All other fields remain scanned,
including facts, evidence text/metadata and clinic documents. UUID-shaped text is not exempt.
Pseudonymisation rules and provider permissions are unchanged.

## Verification

Regression tests use a UUID known to trigger the old regex in both typed fields and prove it
reaches the two-pass chain unchanged. The same string in each untrusted text category is still
rejected before either provider is called. Existing phone/PII rejection tests remain enabled.
