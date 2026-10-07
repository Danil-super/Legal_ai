# ADR 0062: notify every active clinic lawyer when review is required

Date: 2026-10-07. Status: implementation for review; release pending.

The owner authorized immediate Telegram alerts for every HIGH/CRITICAL case,
initially addressed to all active lawyers of the same clinic, until a lawyer
claims the escalation. Existing server-side assignment and discussion access
continue to govern the case workspace.

An AFTER INSERT trigger writes one outbox row per escalation and active
CLINIC_LAWYER membership in the same transaction as the immutable escalation.
The migration also queues retained, unclaimed existing escalations. Composite
foreign keys bind each recipient and escalation to one clinic; FORCE RLS protects
ordinary table access. New lawyers are recipients of subsequent escalations;
adding a lawyer does not resend historical alerts.

The gateway polls a bounded internal claim API every two seconds independently
of the Hermes/analysis profile. It uses the existing dedicated gateway-to-Core
credential. SQL SECURITY DEFINER functions provide narrowly shaped discovery,
60-second fenced leases, eligibility checks and delivery results. PUBLIC cannot
execute them. The ordinary runtime login keeps its existing grants; no bypass
RLS or schema-creation privilege is added.

Eligibility is checked at acquisition and immediately before sending: active
clinic/user/lawyer membership, one unambiguous supported clinic membership,
current clinic subscription, retained case and no claim/resolution event.
Ineligible intents are cancelled. The same predicate applies to CLINIC_LAWYER
recipients; a clinic owner keeps existing workspace/claim access but is not
implicitly subscribed to lawyer notifications.

Messages contain only an opaque case UUID and HIGH/CRITICAL level, with a button
to the server-authorized escalation workspace. They carry no patient narrative,
medical file, reason text, model output or source-law approval. Audit records
contain opaque references, outcome and attempt number. No new external service
or attachment-read authorization is introduced.

Acknowledged delivery is idempotent. A crash/lost acknowledgement after Telegram
accepted a message can produce a duplicate after lease recovery: Telegram
sendMessage has no transactional idempotency key. Delivery is at least once,
not exactly once. A claim/revocation concurrent with the final HTTP send can
leave an already-sent minimal alert; opening its button still runs current
authorization. Transient failures use persisted exponential backoff (5s to 1h)
and Telegram RetryAfter; Forbidden becomes UNDELIVERABLE. Unknown network
outcomes retry rather than falsely recording success.

The outbox cascades with the escalation during the existing controlled retention
purge. Production smoke must verify gateway key/worker registration, retained
queue counts and a synthetic delivery before enabling real-case intake.
