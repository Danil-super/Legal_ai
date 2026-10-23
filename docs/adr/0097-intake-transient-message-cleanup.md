# ADR 0097: Exact transient intake message cleanup

## Status

Implementation of the owner's approved cleanup request, 23 October 2026.
Release requires the normal independent review, complete gates and owner decision.

## Decision

Keep bounded message IDs in ephemeral Gateway user data, never raw text or a
history range. Bind each snapshot to actor, private chat, draft, source revision
and (for background understanding) operation. Record the actual returned waiting
message ID; a synthetic ready callback is anchored on a user message and is not
evidence that its message can be deleted.

After Core staging/save succeeds and the next question is delivered, remove the
previous known question, waiting message and short factual answer (at most 500
characters). Preserve initial descriptions, EVENT narratives, files and summary
confirmations. Guided transitions additionally verify the returned payload/state
and exact next revision. Background staging uses the existing validated receipt.
No deletion is attempted on failed staging/save or failed next-question delivery.

Deletion is best effort with a two-second total bound. Telegram refusals, missing
messages and timeouts must not alter facts or block the next question. Never
guess old IDs after a restart or scan historical messages. Existing messages from
before this feature are not retroactively cleaned up. Navigation and actor-serial
delivery fences remain authoritative; cleanup grants no new access.

## Verification

Real PTB dispatch/watch/conversation tests reproduce the old failures and exercise
quick, interpreted follow-up and guided manual/button transitions. Verify failed
save/send, late delivery after menu and Telegram deletion refusals. No real case
or patient content is required. Date context is separately specified in ADR0095;
neither change alters risk, models, provider, legal approval or patient delivery.
