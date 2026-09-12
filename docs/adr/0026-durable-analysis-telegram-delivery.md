# ADR-0026: Durable analysis jobs and idempotent private Telegram result delivery

- **Status:** Accepted
- **Date:** 2026-09-13

## Context

The sequential PTB conversation dispatcher previously awaited both model passes. One slow
analysis blocked unrelated buttons. In-memory background tasks alone would lose work and
results when the gateway restarts.

## Decision

The gateway sends a private status placeholder to the requesting Telegram user, then persists
its message ID with `POST /v1/cases/{id}/analysis-jobs`. No model call remains in the gateway.
Legal Core owns job deduplication, execution, result retention, tenant authorization and leases.
Callbacks return after the short enqueue request; the existing sequential conversation handling
is unchanged. Status and retry buttons reference opaque IDs; actor context always comes from
the Telegram update, never callback data. Queue results are validated before use.

A lifecycle-owned pooled HTTP client connects only to Legal Core. A PTB JobQueue task polls
terminal notification metadata every five seconds, retrieves each result with actor authorization,
edits the persisted private message and acknowledges successful delivery. A restart needs no
gateway in-memory case state. Delivery edits are idempotent: Telegram's exact not-modified error
counts as success. Deleted/expired placeholders and known blocked/deactivated private chats are
acknowledged without sending replacement duplicates; the canonical result remains retrievable
through status or the original analyze button. Unknown errors and transient failures remain
pending. Telegram RetryAfter suppresses polling until its backoff expires.

At most twenty metadata items are accepted per batch, with four concurrent delivery operations,
a 25-second batch budget and a six-second total HTTP request budget. JobQueue does not overlap
batches. Small per-message locks exist only while requests hold/wait for them; they prevent an
initial QUEUED response or a stale status read from overwriting a delivered terminal result. The
notifier does not read or mutate conversation `user_data`. Logs contain failure classes, never
case facts, provider content, Telegram IDs or raw exceptions.

The auto-edited result is the existing bounded canonical Telegram summary. The button is named
«Результат», not «Полный результат»; the canonical report/PDF remains the source for complete
recommendations and citations. Confirmed early HIGH/CRITICAL routing is acknowledged before PDF
download, so a document delivery failure cannot conceal that the lawyer card already exists.

## Alternatives considered

- An untracked `asyncio.create_task` for model analysis: loses intent on gateway restart.
- Globally concurrent update handling: violates current ConversationHandler assumptions and
  does not provide persistence or result delivery guarantees.
- Sending a new result after each retry: duplicates messages after ambiguous Telegram timeouts.
- A single global edit lock: creates head-of-line blocking between unrelated users. Locks are
  keyed by destination message and reclaimed when unused.

## Verification

`test_analysis_jobs_runtime.py` covers fast enqueue, private destination binding, credential
separation, lifecycle teardown, restart recovery, ambiguous delivery, rate-limit backoff,
malformed responses, and the late-QUEUED edit race. `test_dialog_isolation.py` processes actual
PTB updates through analyze → main menu while the job remains queued. The backend worker tests
prove job execution and tenant/lease behavior; these gateway tests only fake network boundaries.

PTB integration reference: [JobQueue.run_repeating](https://docs.python-telegram-bot.org/en/stable/telegram.ext.jobqueue.html#telegram.ext.JobQueue.run_repeating).
