# ADR 0039: Non-blocking, bounded Telegram analysis diagnostics

Date: 2026-09-21
Status: Accepted

## Problem

The user reports a slow /analysis_status response. Source inspection shows that its
blocking handler waits for the complete Core request before sending any command
response. That request already has a six-second wall deadline, but time spent in
the sequential update queue and Telegram delivery is outside it. A diagnostic
request also delays later updates while it is waiting. These findings explain a
latency path; they are not production latency measurements or evidence of an LLM
call in this read-only command.

## Decision

Keep concurrent_updates(False) and blocking command/callback registration for the
existing ConversationHandler and ApplicationHandlerStop semantics. The short
handler schedules an Application.create_task job and stops routing the update.
The supervised job acknowledges a callback, sends a generic progress message,
fetches the authorized Core diagnosis, and edits only its own progress message.
It never edits the old menu or writes wizard state. An expired callback or failed
progress delivery does not suppress the subsequent result attempt.

Coalesce overlapping command/refresh requests by private chat and actor while a
job is pending; release the slot on success, failure or even pre-start cancellation.
Retain at most eight diagnostic jobs per gateway, without changing existing API
rate limits. No result cache is introduced: a later refresh must reauthorize at
Core. Owner, active subscription and tenant checks remain server-side and unchanged.

Retain the six-second Core wall deadline, including response streaming and decoding.
The UI distinguishes timeout from disabled analysis and does not claim that keys
or models were tested. Telegram callback acknowledgements have a one-second budget;
progress/edit/fallback deliveries each have a three-second budget. Those network
budgets are separate from the Core deadline, not a six-second end-to-end promise.
Failed delivery is logged with a fixed message, never response bodies or secrets.

Bound streamed Core responses to 16 KiB before decoding; require identity encoding
and do not follow redirects. The only diagnostic request remains GET
/v1/analysis-diagnostics with the requesting actor's identity. No provider call,
case mutation, approval, risk-policy change or external patient message is added.

## Verification

The added tests use the real composed PTB application with Telegram transport
mocked. They hold a Core request pending, verify progress, /menu and another user's
/whoami, preserve sequential dispatch, coalesce refreshes, isolate users, and check
cancellation, capacity, group denial and Telegram delivery failures. HTTP transport
tests cover owner denial, old Core versions, redirects, malformed responses, slow
stream cancellation, stream closure and limits before buffering/decompression.

Final quality, integration, security and deployment status must be read from the
workflow for the committed revision. These synthetic tests do not measure the
user's Telegram proxy, live server response time or model readiness.
