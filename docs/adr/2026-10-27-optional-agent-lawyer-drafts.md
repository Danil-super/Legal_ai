# Optional Agent assistance after durable lawyer routing

Status: owner-approved AIF08 Agent-only implementation, disabled by default.

V2 HUMAN_REVIEW is already committed by Core with its escalation and notifications.
The Agent returns that same receipt, whether optional assistance succeeds or fails.
It never converts HIGH/CRITICAL into an automatically allowed canonical report.
Legacy analysis and other V2 outcomes retain their existing path.

When `AI_FIRST_LAWYER_DRAFT_ENABLED=1` and the actual paired analysis-job lease
is present, the Agent first calls the private work-product lookup using the
retained preparation operation, fact hash and policy. A retained view skips all
new model work. Otherwise it fetches the separately authorized source-bound
context, uses the existing LawyerDraftGenerator with the same researcher/reviewer
profiles, and submits only exact source, claims and semantic reviews. Core owns
verification, storage identity, review status and citations. Unsupported reviews
remain unchanged for Core to reject; they are not silently removed. Abstention
does not produce a plan or a storage submission.

The shared deployment flag is 0/1, default 0; Agent also accepts `true` for
compatibility with the Core test/operational convention. Other values are off.

Private client methods reparse metadata and typed responses, reject mismatched
case/source/fingerprint/policy bindings and strip input-bearing error contexts.
Only lookup accepts JSON null. Every request carries the existing Agent key,
Telegram actor, job ID/token and idempotency key equal to the job ID. Neither
clinic selection nor arbitrary case materials come from the caller or model.

Optional work has its own 110-second maximum wall budget, further restricted to
the remaining Agent 115-second overall wall minus a one-second return margin.
It therefore fits the unchanged worker HTTP 120 seconds, wall 125 seconds and
fresh job lease 180 seconds. The Hermes profiles retain their existing stage
timeouts; no heartbeat, new queue, external service or model configuration is
introduced. No Agent transaction holds database locks during model I/O. An
optional deadline or provider/Core refusal preserves the durable HUMAN_REVIEW.

An HTTP retry looks up a stored draft before generating again, including when a
submission committed but its HTTP response was lost. Concurrent requests can
still both start model work before either stores a version; durable uniqueness
and final current-source checks belong to Core. The existing Core worker's
receipt-first recovery shortcut requires a separate follow-up to retry unfinished
optional assistance after a worker crash; this Agent-only slice does not change
that worker or claim end-to-end crash recovery or production activation.

Tests exercise actual Agent HTTP and real HTTPX/Hermes clients over synthetic
MockTransport responses: default-off behavior, urgent levels, early/late receipts,
fencing, malformed/foreign responses, abstention, refusals, timeouts and replay.
No live providers, production data or legal approval are used as test fixtures.
