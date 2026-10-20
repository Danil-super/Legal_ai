# Automatic Telegram delivery of intake preparation

Status: implementation, not release approval. Requested by the owner on 20 October 2026.

The durable Core operation already runs asynchronously, but the Telegram UI previously
required a manual refresh even after success. A bounded read-only watcher now delivers
one internal refresh through the existing actor-serial update processor. Ordinary
handlers recheck current ownership, operation, revision, and cancellation before use.
The internal callback cannot assert user consent or start another model invocation.

The watcher retains only routing identifiers and an authenticated client. It is bounded
to 64 concurrent selections and 90 polls, stops when the selected operation changes,
and is awaited by the running PTB application during shutdown. Legacy manual refresh
remains supported for restart and transport recovery. A completed proposal has only
confirm, edit and menu controls; technical fallback actions belong to pending states.

This slice does not change model/provider, risk policy, legal approvals, factual
confirmation, or report generation. The later provisional-draft/single-summary slice
must establish its own contract and tests before activation. The PTB reproduction
tests cover automatic initial/follow-up delivery and an in-flight menu/cancel race.
