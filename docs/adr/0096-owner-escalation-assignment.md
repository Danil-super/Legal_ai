# ADR 0096: owner-mediated assignment of unfinished escalations

## Status

Accepted by the owner, 21 October 2026, including the explicit assignment permission.

## Context

Production case 26 was claimed by the owner, not by the testing lawyer. Existing
claim correctly refuses another assignee; replacing that protection would permit
lawyer takeovers. The old card additionally exposes raw field names/JSON.

## Decision

Keep claim/resolve authority unchanged. Add an owner-only assignment endpoint:
`POST /v1/case-escalations/{id}/assign`, body `assignedMembershipId` and required
nullable `expectedAssignedMembershipId`. The target is an active same-clinic lawyer
with an active user and current entitlement. Invalid/foreign resources are 404;
unavailable lawyer, closed/resolved case or stale assignment are 409. No assignment
or audit write on rejection. A repeat when the current assignee already equals the
target returns current detail without another event; this is state-idempotence,
not a guarantee of replaying an old operation after intervening assignments.

Serialize claim/assignment/resolve on the existing escalation lock. Preserve true
actor identity in append-only history: new ASSIGNED event stores owner as actor
and the target in a separate nullable same-tenant foreign-key column. Old events
remain unchanged; all detail/queue readers use the new target when present.
Assignment audit contains only membership IDs, never patient narrative. Additive
Alembic migration preserves RLS, immutable-event trigger and runtime-role grants.
Rollback must retain compatible readers after ASSIGNED events exist; destructive
downgrade is refused while such history exists.

Owner-only roster adds optional membershipId for safe target selection. Detail
adds optional assignedRole to distinguish an assigned owner from another lawyer.
Telegram offers choose → confirm → assign, keeps the observed expected assignee,
rechecks server authority, and does not embed two UUIDs beyond the callback limit.
Stale/forged callbacks cannot authorize reassignment. An ordinary lawyer sees no
transfer action; the assigned lawyer can discuss and resolve as before.

Use the already tested readable fact/report presenters for preview and full card.
No LLM, clinical facts, legal recommendations, approved sources, risk policy,
subscription rules or automatic patient delivery change in this fix.

## Verification and release

Tests cover owner→lawyer, queue/detail consistency, final resolution, safe retries,
stale/foreign/revoked/inactive/closed cases, migration constraints and Telegram
confirmation. Production deployment/actual assignment are separate from fixture
tests; do not claim them before they are performed. Do not rewrite historical dates.
