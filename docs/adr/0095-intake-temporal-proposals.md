# ADR 0095: Server-bound date for intake interpretation

## Status

Accepted for the owner-approved temporal-context work, 20 October 2026.
First increment only: initial private date context. Multi-field proposals and
semantic episode validation remain separate, incomplete increments IL03–IL06.

## Context

Initial understanding sends only source text and tells the model to omit relative
dates. Later-answer intents already have a server-bound asOfDate. The initial
interpreter also checks future proposed dates against its host clock; a retry can
therefore change which proposal is acceptable. A valid literal date anchor does
not, by itself, prove the right service or complaint episode.

## Decision

Extend private IntakeUnderstandingContext with optional asOfDate. Only Core may
supply it; invocation/request DTOs still reject it. Dates accept an ISO calendar
date or a Python date, never timestamp/boolean/datetime coercion. Legacy contexts
without the field retain None rather than inventing a reference.

Core initially derives the reference from the persisted operation creation time
in UTC. A context read, retry or restart never substitutes its current clock.
This anchors the understanding operation, not proof of the original Telegram
message date for old drafts resumed before any operation existed. Such historical
source-time provenance remains a separate concern; do not claim it is established.

Agent forwards this date to the model and checks future proposals against the same
bound calendar. Without it, keep the legacy source-only request and omission of
relative dates; only the legacy absolute-future guard uses its existing host date.
No universal semantic guarantee follows from a prompt or an accepted date span.

## Compatibility and rollout

The Agent requests the optional calendar with `?includeCalendar=true`. The private
Core endpoint omits `asOfDate` entirely unless requested (not even a null key).
An old strict Agent therefore retains its exact response shape on new Core; an
old Core ignores the new query parameter and a new Agent accepts its old shape.
This allows either rollout/rollback order without a mixed-version parser failure.
The flag requests representation, not a caller-selected date or new access.
Existing operation hashes, public receipts, snapshots and database schema do not
change. No migration, backfill, legal approval or production release in this slice.

## Consequences and verification

The date remains outside untrusted source text and outside the public model
invocation. Test schema rejection, authenticated forwarding, UTC/year-boundary
replay with real PostgreSQL and a host clock different from the supplied date.
Relative-date interpretation still needs model/episode tests, and later-answer
multi-field reuse is not implemented by adding this field.
