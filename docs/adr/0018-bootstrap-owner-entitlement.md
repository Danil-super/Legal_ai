# ADR-0018: Bootstrap owner receives an initial service entitlement

## Status

Accepted

## Context

The configured bootstrap owner creates the first clinic membership, while the subscription grant
endpoint requires its caller to already hold an active entitlement. Without an initial entitlement,
the owner cannot reach the endpoint that would allow subscriptions to be issued.

## Decision

- During the existing server-configured bootstrap, create an active, non-expiring `MVP_MANUAL`
  entitlement for the owner only when no entitlement exists for that owner and clinic.
- Record the creation through the existing append-only entitlement event mechanism.
- Do not reactivate, replace, or extend an existing entitlement. Suspension and cancellation
  continue to be explicit operational decisions.

## Consequences

- A newly configured platform owner can access the subscription administration flow immediately
  after a deployment.
- Restarting Legal Core remains idempotent and does not create repeated entitlement events.
- The change adds no new patient data, payment data, or external integration.
