# ADR-0029: Durable analysis jobs keep Telegram responsive

## Status

Accepted for implementation under the owner's six-point reliability remediation request.

## Context

Telegram's sequential ConversationHandler awaits the two-model chain inside one callback.
An unrelated user's next menu action therefore waits for that analysis. Turning on global
concurrency would instead race mutable conversation state. An in-memory fire-and-forget task
loses acknowledged work on process restart.

## Decision

Legal Core owns PostgreSQL `analysis_jobs`, with tenant RLS and composite case/membership
foreign keys. The authenticated gateway creates an intent and receives HTTP 202. Concurrent
clicks serialize on the case and share the active job; success is replayable. A failed job may
be retried explicitly as a new intent after the old worker has lost submission authority.

The existing Legal Core process runs two async consumers, each with independent database
sessions and a shared bounded HTTP pool to the already configured orchestrator. A narrowly
scoped SECURITY DEFINER function claims one queued/expired job with SKIP LOCKED, a random
lease token and a 180-second lease. It is not callable by PUBLIC. The runtime role still
cannot bypass RLS. No new broker, external service or provider credentials are introduced.

Each claimed job's UUID is its analysis submission idempotency key. The orchestrator forwards
the lease ID/token to Legal Core; submission verifies actor, tenant, case, token and expiry
under row lock before accepting a result. A stale worker cannot commit after takeover.
Recovery first checks the existing immutable submission response, avoiding new model calls
when a result was committed before an HTTP failure or crash. Up to three process-interruption
claims are allowed. Ordinary provider/validation failures become terminal and visible.

The queue stores operational references and status-message ID, not another copy of facts,
model text or PDF bytes. Completed data remains in existing idempotency/report storage and
is subject to existing case retention. Status access rechecks current membership, subscription
and case retention. A bounded internal-key-protected notification list returns metadata only;
the gateway separately reads each authorised result, edits the original private status message,
then acknowledges delivery. Repeating an edit is safe after an acknowledgement timeout.

## Alternatives

- Global Telegram concurrency: rejected because ConversationHandler and shared user_data need
  ordered transitions; only long independent work is moved out of the update handler.
- In-memory task alone: rejected as the source of truth; useful only for transient PDF delivery.
- New Celery/broker service: unnecessary infrastructure for two bounded workers on this VPS.
- Holding a database transaction across model calls: rejected because it consumes pool capacity
  and locks; the lease spans short transactions instead.

## Consequences and limits

Model latency still exists, but no longer determines callback queue latency. Worker duration
and sanitised outcome are logged without request text, provider body or credentials.
Ambiguous provider HTTP failures cannot promise exactly-once provider billing. Lease fencing
and result replay prevent stale submissions and repeated completed reports, not arbitrary
upstream billing effects. No automatic patient message or normative approval is enabled.

Deploy additive schema first, then matching Core/orchestrator/gateway. Downgrading the jobs
migration refuses to run while work is queued/running. Normal application rollback retains
the additive table; drain/inspect jobs before disabling the worker.

References: [PostgreSQL SELECT locking](https://www.postgresql.org/docs/16/sql-select.html),
[PTB ConversationHandler](https://docs.python-telegram-bot.org/en/stable/telegram.ext.conversationhandler.html).
