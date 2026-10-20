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

Deploy updated Core/Agent contract readers before the new Core producer. An old
strict Agent rejects the additional response key; therefore reverting Agent alone
while the new producer runs is not a compatible rollback. Revert/disable the Core
producer first, or retain the new reader. New readers accept old context shapes.
Existing operation hashes, public receipts, snapshots and database schema do not
change. No migration, backfill, legal approval or production release in this slice.

## Consequences and verification

The date remains outside untrusted source text and outside the public model
invocation. Test schema rejection, authenticated forwarding, UTC/year-boundary
replay with real PostgreSQL and a host clock different from the supplied date.
Relative-date interpretation still needs model/episode tests, and later-answer
multi-field reuse is not implemented by adding this field.
