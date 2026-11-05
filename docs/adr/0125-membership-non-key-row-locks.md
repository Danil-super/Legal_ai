# Use non-key membership locks alongside report provenance fences

Status: accepted, 2026-11-05.

A normal report flush takes FK KEY SHARE locks on its actor membership. If the
same actor is a legal editor who approved selected evidence, a concurrent member
writer can hold the target User FOR UPDATE, wait on that membership FOR UPDATE,
and block the report's final source User FOR SHARE. PostgreSQL detects a cycle
and aborts one request. Subscription grants and bootstrap promotion can create
the same cycle through their membership selects or the entitlement helper.

Retain the existing target User lock, advisory serialization, fresh membership
reads and every approval/authorization/owner-protection rule. Use PostgreSQL
FOR NO KEY UPDATE for the existing membership locks in clinic member updates,
platform grants, entitlement provisioning and bootstrap admin promotion. These
paths update role/status only, or read them to authorize provisioning; they never
change membership id, clinic_id or user_id, which are the unique/FK identity keys.
The lock still excludes competing role/status writers and FOR SHARE readers while
allowing report/audit FK KEY SHARE locks. No source User lock is weakened.
SQLAlchemy renders this mode with `with_for_update(key_share=True)` and the default
`read=False`; adding `read=True` would instead request FOR KEY SHARE.

Real PostgreSQL tests pause normal V1 persistence after its actual FK flush and
concurrently exercise the three identity writer entry points. The actor is also
an approver of selected legal evidence. The writers complete without deadlocks,
role changes still fail the report's final actor guard, and retry under current
access succeeds. Existing single-membership and owner-protection regressions
remain mandatory. This changes row-lock compatibility, without schema or policy
changes.

References:
- https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-ROWS
- https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.GenerativeSelect.with_for_update
