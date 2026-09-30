# ADR-0049: Guided intake and isolated anonymised case materials

- **Status:** Accepted
- **Date:** 2026-09-30

## Context

The legacy wizard asks a clinic administrator to supply legal labels, claim dates
and conclusions that they may not know. The owner approved an ordinary-language,
seven-step description flow and optional anonymised material before its factual
summary is confirmed. The existing clinic document library is not a suitable
shortcut: it is a reusable clinic knowledge base, whereas case material is
short-lived, case-specific and must not be retrieved as legal evidence.

## Decision

New drafts use the versioned v2 state machine in
`SPEC-guided-case-intake-v2.md`; v1 drafts continue through their original
handler. Legal Core creates the summary and sufficiency result deterministically,
not through an LLM. Unknown is a first-class result, never converted to a negative
fact or a legal conclusion.

Optional anonymised materials receive their own tenant-scoped metadata and private
object-storage ownership, with an expiry inherited from the existing draft/case
retention policy. They are never placed in the legal corpus, clinic library,
evidence retriever, model prompt, audit payload or process log. Every read/write
requires both resolved tenant context and permitted membership; a role such as
LEGAL_EDITOR does not grant access.

## Consequences

The v2 experience is safer for non-lawyers and makes gaps visible for a targeted
follow-up or authorised lawyer escalation. It also adds a sensitive data category,
so implementation requires the reviewed material contract, migration, RLS, bounded
parsing, authenticated streaming and deletion reconciliation before any upload
route is enabled. The owner has authorised only anonymised material; real patient
data remains outside the production-pilot permission boundary.

## Alternatives considered

- Continue the legal-label questionnaire: rejected; it delegates legal
  qualification to the clinic user and produces unreliable facts.
- Store uploads in the clinic document library: rejected; it would confuse
  temporary case material with reusable approved clinic context and break
  retention/access boundaries.
- Send raw material to Hermes for interpretation: rejected; Legal Core remains
  the legal truth boundary and no external processing was approved.
- Treat uploaded files as proof: rejected; material is context for a human
  workflow, not date-applicable approved legal evidence.
