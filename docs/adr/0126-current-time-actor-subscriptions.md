# Evaluate actor subscriptions at the current database time

Status: accepted, 2026-11-05.

The actor resolver used transaction-start `now()` for subscription start/end
bounds. After a real lock wait or another statement, a renewed subscription whose
start moved forward could be incorrectly rejected, while one that expired during
the wait could remain accepted. The final analysis access guard already requires
current time, but its resolver call inherited the transaction-start check first.

Use PostgreSQL `clock_timestamp()` for both bounds in `resolve_actor`. Keep all
ACTIVE, identity, tenant, role and entitlement rules unchanged. A current renewal
with the same actor can finish its report; role changes still fail the final
actor-context comparison, and an expired subscription still fails closed.

PostgreSQL tests hold an actual runtime transaction across database waits and an
independent renewal/expiry update, then resolve the actor again. The concurrent
normal-report/platform-renewal test requires success without deadlock or a false
subscription rejection. Role-change rollback and retry checks remain intact.

References:
- https://www.postgresql.org/docs/current/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT
