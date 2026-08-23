# ADR-0009: Configured platform owner grants subscription access

## Status

Accepted

## Date

2026-08-23

## Context

The service is operated as a subscription SaaS for clinic administrators. The owner needs a
simple, controlled way to activate a customer after a sale without database access or a shell
command. The Telegram interface alone is not an authorization boundary: command arguments are
untrusted and users can forge requests to a reachable API.

## Decision

The bot offers `/grant_access <Telegram_ID>`. It forwards the caller identity and target numeric
ID to a private Legal Core endpoint with an idempotency key. Legal Core is the authority: it first
requires an active administrator entitlement, then compares the caller Telegram ID with the
server-side `PLATFORM_OWNER_TELEGRAM_ID` configuration. A non-owner receives `OWNER_REQUIRED`.

For a target with no active administrator membership, Legal Core creates a separate default
clinic and a `CLINIC_ADMIN` membership, then provisions `MVP_MANUAL` access. For exactly one
membership it updates that clinic's entitlement. More than one membership is rejected with
`TARGET_ADMIN_AMBIGUOUS`; no clinic is chosen implicitly. This path cannot grant `LEGAL_EDITOR`,
does not store payment information and writes the existing append-only entitlement event. A
transaction-scoped advisory lock on the target Telegram ID serializes concurrent first grants.

The endpoint remains private to the Telegram gateway/local operator boundary. Publishing it or
adding a payment provider requires a separate service-authentication and product-security
decision.

## Consequences

- The owner configuration is deployment secret/configuration, not bot data and not a request
  parameter.
- Grant retries are safe through the existing idempotency records.
- The owner must first be onboarded and hold an active entitlement.
- Access suspension/cancellation and multi-clinic resolution remain deliberate support operations.
