# Preserve the actor identity when granting subscriptions or adding members

Status: accepted, 2026-11-05.

The actor resolver requires exactly one ACTIVE membership with a supported clinical
role (CLINIC_OWNER, CLINIC_ADMIN or CLINIC_LAWYER). Subscription grants previously
searched only administrators, creating another clinic for an existing owner or lawyer.
Adding a clinic member also allowed an active member of another clinic to acquire a
second identity. Both operations could succeed while making subsequent login fail.

Both writers keep the existing per-Telegram-ID transaction advisory lock for initial
creation and target User row lock. They refresh ORM state after locking and inspect
the same active role set as the resolver before deciding to create a membership.
A subscription grant renews the existing supported membership without modifying
its role, clinic, ID or history. Existing ambiguous identities remain a conflict
(the historical TARGET_ADMIN_AMBIGUOUS error code stays compatible).
A clinic add returns 409 TARGET_MEMBERSHIP_CONFLICT when another clinic already
owns that active identity. It never transfers a member or revokes foreign access.
Same-clinic changes, owner protection, and inactive history retain existing semantics.

No database roles, RLS policies, subscription approval rules or resolver authorization
rules change. This serializes the two API writers; direct administrative SQL remains
responsible for preserving the existing invariant.

PostgreSQL regressions exercise renewal and actor resolution for each supported role,
foreign-clinic conflicts and concurrent writers using the least-privileged runtime role.
SQLAlchemy's populate_existing refreshes objects in the identity map when a SELECT
returns after waiting for a lock:
https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#populate-existing
