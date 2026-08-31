# ADR-0011: Provide owner-granted, time-limited free pilot access through entitlements

## Status

Accepted

## Date

2026-08-31

## Context

The initial rollout needs selected clinic administrators to use the product without payment
processing or public self-registration. Access must remain specific to one Telegram user and one
clinic, expire automatically and preserve every existing tenant, evidence, risk and verifier
boundary.

## Decision

Legal Core accepts a `FREE_PILOT` plan only from the existing platform-owner grant endpoint. A
grant requires an explicit duration of 1 through 90 days. The Telegram admin panel offers a
30-day pilot convenience action; the owner-only command may choose a value within the same
range.

The grant is stored in the existing tenant-scoped `subscription_entitlements` record with a UTC
`starts_at` and `ends_at`. No new table, identity attribute, payment record or registration path
is added. The existing active-and-time-valid entitlement resolver remains the sole gate for bot
access, so an expired pilot is denied exactly as any other inactive entitlement.

The subscription-grant contract is extended additively with `planCode`, `pilotDays` and `endsAt`.
The original `MVP_MANUAL` behaviour remains available as the default and has no expiry.

`FREE_PILOT` grants do not approve legal material, create legal-editor rights, enable an LLM or
Hermes, weaken approved-only retrieval, change risk policy, or permit automatic patient messages.
Recommendations therefore remain blocked until their separate legal and security approvals are
complete.

## Alternatives considered

### Open public registration

Rejected because it would introduce a materially different identity, abuse-prevention and access
control decision before the pilot is validated.

### Permanent free entitlement

Rejected because it makes controlled-pilot access difficult to review and revoke by normal expiry.

### Separate pilot-access store

Rejected because it would duplicate the existing entitlement guard and increase the chance of
different authorisation behaviour for free and paid access.

## Consequences

- The platform owner can enrol a selected administrator without payment processing.
- Each pilot access grant is auditable through the existing entitlement/audit mechanism and is
  bound to the selected clinic.
- API validation rejects missing, non-pilot or out-of-range pilot durations before persistence.
- PostgreSQL API and Telegram interaction tests prove grant, expiry metadata and owner flow.
- A later self-service, invitation or billing feature requires a separate decision and review.
