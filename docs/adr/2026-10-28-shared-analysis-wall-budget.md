# Shared reasoning wall budget (2026-10-28)

Status: implemented for the authorized clinic test release.

A read-only production synthetic case required 68.64 seconds for research and
8.97 seconds for review, completing in 82.36 seconds with every claim accepted
by Core. The separate 65-second researcher cap discarded this otherwise
in-budget result. The service already gives the entire analysis 115 seconds.

Reasoning clients now allow a call to use that existing 115-second envelope.
The route retains one outer `asyncio.timeout(115)` across context retrieval,
both model passes, submissions and any optional lawyer draft. The second pass
uses only the remaining outer time, never a new overall budget. Existing lawyer
draft limits remain `min(110, parent_remaining - 1)` and preserve urgent routing.
Intake and subsequent factual interpretation use a separate client capped at
65 seconds. Clients and endpoints hold no mutable per-request deadline.

Worker HTTP/wall timeouts (120/125 seconds), the 180-second lease, models,
reasoning high, source verification and submission freshness guards are unchanged.
Cancellation continues to close model streams; no new task or background write
is created. This removes an artificial per-stage cutoff, not the total bound,
and does not guarantee that every provider response will meet it.

Tests exercise actual streaming Hermes clients through the ASGI route with
scaled 1/2-second budgets: a first pass beyond the former cap can succeed, while
combined passes beyond the existing total cancel the reviewer without a Core
submission. Existing context/reasoning/submission and urgent-draft cancellation
coverage remains active.
