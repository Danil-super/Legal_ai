# ADR-0014: Discuss escalated cases through a tenant-scoped internal thread

## Status

Accepted on 2026-09-04.

## Context

`HIGH` and `CRITICAL` assessments must reach a clinic lawyer without exposing the lawyer's
personal Telegram account, creating a public invite link, or placing the case description and
medical documents into ordinary chat. A public case number, risk level and deterministic reason
codes are sufficient to identify an internal review task. They are not sufficient to recreate a
patient identity.

The user interface needs a reliable pointer from the server-created risk escalation to this review
thread. Deriving an identifier from a `case_id`, forwarding a Telegram callback, or trusting a
client-supplied clinic identifier would bypass the tenant and role checks.

## Decision

Legal Core returns `escalationId` only with an already server-created `escalationRequired=true`
analysis result. The response contract rejects an absent ID for a required escalation and rejects
an ID for every other result.

Legal Core also exposes a de-identified escalation queue: public case number, `HIGH`/`CRITICAL`
level, deterministic reason codes and time. Clinic owners and clinic lawyers can see the queue of
their clinic; a clinic administrator can see only escalations for cases they created. The server
sets tenant context and rechecks the same rule when a thread is opened or a message is appended.

The Telegram gateway opens the internal thread from that opaque ID. The thread accepts only
bounded text and rejects obvious direct identifiers before persistence. It does not accept files,
medical documents, patient contact details, personal links, or automatic external delivery. The
lawyer and administrator exchange concise, de-identified questions and answers in the product;
case material requiring protected storage remains outside this MVP flow.

## Consequences

- critical-case review is available to a lawyer without sharing a personal account or external
  link;
- a user cannot enumerate another clinic's queue or thread by changing a callback value;
- the analysis response remains a pointer to a server-side resource, not a transport for case
  text;
- formal legal conclusions and patient-facing answers still require the existing human-review and
  no-auto-send controls.
