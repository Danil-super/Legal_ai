# Retain discussion and resolution operations across uncertain HTTP responses

Status: accepted, 2026-11-05.

Discussion and resolution previously had no operation receipt. A lost response
after commit made a retry insert another discussion message, or report an already
completed resolution as an error.

Both existing POST routes accept an optional UUID `Idempotency-Key`. Existing
keyless callers retain their original behavior; every new Telegram operation
supplies a key. An operation is scoped to action, escalation, server-resolved
clinic and actor membership. The normalized body and escalation/action bind its
request hash. Reusing the key with another body returns 422 without a write.

Case then escalation row locks serialize both first delivery and replay. Replay
rechecks active actor, current tenant visibility, content retention and, for
resolution, current lawyer role and assignment. The receipt cannot bypass a
revoked subscription, foreign case or expired content. A matching completed
operation can be read after resolution; a new operation remains forbidden there.

The existing generic `idempotency_records` table stores only a message-ID pointer,
atomically with the message, workflow event and audit. Its scope, resource types
and RLS contract already support these records: no table or migration is added.
Replay also checks the actual tenant/author/message body and metadata-only audit
binding. No raw body is duplicated into receipts, audit metadata or gateway
pending metadata. Existing immutable records are not rewritten.

The Telegram operation UUID derives from original private message ID, actor,
escalation and action. A transport/invalid-response retry uses the identical key,
body and actor, at most once automatically, with existing HTTP timeouts unchanged.
An uncertain manual repeat retains that binding; a new text uses a new intent.
An explicit recovery button replies to the original Telegram message. Its source
ID and the original private sender let a restarted gateway reconstruct the same
operation without storing raw message text. Missing or unverifiable originals
stop with a route to case history rather than creating a new message.

Tests cover the original RED duplicate, real PostgreSQL concurrent requests,
payload mismatch, current access/retention, malformed keys, and Gateway → actual
Core commit → simulated ReadTimeout → replay, including empty gateway memory.
They prove persistence/recovery contracts; they do not prove provider quality,
real Telegram network delivery or arbitrary process-crash recovery.
