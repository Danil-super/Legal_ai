# ADR 0061: Explicit guided-v2 routing under risk-policy.v3

Date: 2026-10-07. Status: implementation preparation; activation pending human review.

## Context

The production predecessor is dental-risk.v1 at 50,000 RUB. Guided-v2 records
HEALTH_CONSEQUENCE_SIGNALS and INCOMING_COMMUNICATION; v1/v2 read legacy safety
facts instead. An explicit hospitalization or court/authority-document selection
therefore does not currently create urgent routing. The owner authorised preparing
and testing a direct v1 to v3 transition, without intermediate v2 activation.

## Decision

Add the exact opt-in risk-policy.v3 payload from SPEC-risk-policy-v3.md. Its
capability flag is false by default in the domain model. Only v3-enabled,
confirmed GUIDED_V2 snapshots can supply new signals. Closed enum matching is
deterministic; free text, filenames, model output and uploaded bytes supply none.
Hospitalization and a reported court/authority document route CRITICAL. Preserve
both reasons in stable order and deduplicate a dimension represented in both
guided and legacy facts. Both existing confirmation endpoints reuse the same
tenant-scoped early-triage persistence before any retrieval or model call.

Unknown, invalid, contradictory and NO_KNOWN_INFORMATION answers never supply
negative legacy facts. They block final clearance and identify the existing
follow-up question. Early urgent routing survives a later unavailable analysis.
The exact monetary HIGH boundary is 5,000,000 kopecks, inclusive.

The human approval CLI explicitly requires the new capability, early triage,
direct-v1 attestation and supersession flags. In its transaction it locks the
approved predecessor and requires version 1, exact v1 payload/threshold/hash and
matching prior approval event. Any mismatch rolls the transaction back. The
existing database guards and unique active-policy invariant preserve immutable
history. No new table or migration is needed. The CLI also runs both the existing
synthetic compatibility gate and the new guided-v2 gate before touching the DB.

This supersedes the v1-to-v2 activation sequence in ADR 0024; its historical
decisions and v1/v2 payloads/outcomes remain unchanged. Deploying this code does
not approve or activate any policy. An active LEGAL_EDITOR must explicitly
confirm the exact v3 hash and triggers after the release checks.

## Verification and release limits

Tests cover exact enum/type matching, urgent precedence, negative/positive
conflicts, unknown abstention, threshold boundaries, strict parser/hash/flags,
legacy behavior, human-only direct activation, rollback on predecessor mismatch,
both real API confirmation paths, tenant denial and idempotent escalation.

This preparation slice does not make guided LOW/MEDIUM usable. The current
questionnaire cannot record an explicit negative health answer and omits legacy
safety facts. tasks/risk-policy-v3.md describes the next factual intake slice.
No automatic legal approval, model activation or production writes occur here.

Production activation is blocked until an operational safe-stop is implemented
and exercised: stop new automated analyses with a v3-compatible application,
preserve drafts and urgent escalations, retain authorised lawyer access and show
manual review/retry for blocked confirmations. DEPLOY_ANALYSIS_ENABLED=0 alone
is not claimed to satisfy that gate. Retired v1 cannot be reactivated; recovery
requires a separately reviewed monotonic policy version, at least v4. Rolling
back to an application that cannot parse v3 is invalid after activation.
