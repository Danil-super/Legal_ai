# ADR-0008: Subscription access is separate from legal editing

## Status

Accepted

## Date

2026-08-22

## Context

Dental Legal AI is a subscription service that helps clinic administrators process an incident.
It does not supply the clinic with its own lawyer. A clinic membership identifies a tenant but
does not establish that an individual customer has purchased or still has the right to use the
service. Earlier operational language risked conflating a customer administrator with the
internal `LEGAL_EDITOR` role that releases legal-corpus versions.

## Decision

An entitlement belongs to one `user_id` and one `clinic_id`, is time-bound and has an explicit
state. Legal Core resolves the Telegram user to one active clinic-administrator membership,
sets server-owned tenant context, then requires an active entitlement before every protected
operation. Missing, suspended, cancelled or expired access returns `SUBSCRIPTION_INACTIVE`.

Initial provisioning is internal after a verified purchase. The service stores no card data and
does not call a payment provider. The entitlement lifecycle is auditable without raw patient
data. `LEGAL_EDITOR` remains a platform-only role and subscription provisioning cannot grant or
change it. Legal version approval remains manual and checksum-bound.

## Consequences

- A mapped administrator cannot use a case or legal-retrieval endpoint until access is granted.
- A single customer may later have separate entitlements for several clinics, though the MVP
  Telegram flow requires one active administrator membership to avoid accidental tenant choice.
- Payment checkout, provider webhooks, refunds, invoices and self-service subscription changes
  are explicitly outside this decision and require a separate security/product review.
- The Legal Core API must stay private to the gateway or gain an approved service-authentication
  boundary; a user-controlled Telegram ID header is not a public authentication mechanism.
