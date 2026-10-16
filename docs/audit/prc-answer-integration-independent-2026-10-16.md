# Independent review: subsequent-answer integration

Reviewed runtime: `a82ed5e`; the final PostgreSQL run also includes the test-only
`ff64feb`. Branch: `review/final-answer-integration-20261016`. This continues the
separate reservation/consent review recorded in
`prc-answer-operations-independent-2026-10-16.md`.

## Scoped verdict

No required correctness/security defect was reproduced in the new API, private
context, lifecycle, shared runner dispatch, callback fences or default-off factory
composition. This review does not independently reapprove the reviewer's own pure
answer helpers. It does not assert live model semantic quality, legal completeness,
deployment or final project-wide release readiness.

## Exact factory review

The factory change was reviewed independently before its exact baseline pin update:

- old blob prefix: `b1afe5e`;
- reviewed new blob: `6c7145f1a2a29d39a8cafa89d2dd15a5416729cf`;
- file: `services/gateway/telegram/src/telegram_gateway/case_experience_runtime.py`.

`enable_answer_interpretation=False` is a separate explicit opt-in and requires
`enable_understanding=True`. The guard executes before creating the app. The answer
handler is installed only in that app and inside the existing optional preparation
branch. `main()` still calls the factory without either flag. No personal entry
point, provider, role, risk threshold, retention policy or default public feature
was enabled by this change. The reviewer ran the three new composition tests on
the exact uncommitted reviewed blob: all passed.

## Runtime evidence

Two independent executions passed without skips:

1. **118 tests, 2.80 seconds**: actual PTB answer runtime/client/selector/composition,
   Agent metadata API and context client, plus personal/default clinical boundary
   canaries. Includes five newly authored independent PTB tests.
2. **96 tests, 27.10 seconds**: Core public/private answer routes, lifecycle,
   answer/narrative runner and all sixteen real Core↔Agent HTTP/PostgreSQL cases.
   The model itself is synthetic; there were no provider calls.

The five new independent PTB tests prove:

- enabling one full app does not modify existing default or initial-understanding
  apps, including their original conversation callbacks;
- a committed start whose response is lost is discovered after RAM reset without
  reposting the source;
- an unapplied DELETE failure cannot clear the current draft or claim cancellation;
  a subsequent successful cancellation can still finish;
- an owned operation cannot be accepted through a callback naming another draft;
- replaying an old accepted card preserves a genuinely later explicit navigation
  checkpoint and does not create an analysis job.

The PostgreSQL run used the least-privilege runtime identity at `127.0.0.1:55486`,
disposable database `dental_legal_test_answer_lifecycle_20261016`, only after the
previous reviewer reported the cluster free. No role bootstrap or production
operation occurred. The cluster was released after the run.

JUnit artifacts (local review cache):

- `final-answer-integration-review-a82ed5e.xml`, SHA-256
  `5ea7a4b11595d66aa4d86feb7cef0e108c6a7883991c941885da9c3fcd4b7851`;
- `final-answer-integration-pg-ff64feb.xml`, SHA-256
  `3fcaf1ad3336f1434bc137ef72b317a3c9f106d1a2748b8f2bb5188af2666853`.

Both are under `/home/danil/.cache/dental-prc-verify.pLetxJ/`.
The PTB run emitted 53 existing conversation-handler/job-queue test warnings.
Ruff passed for the new test file.

## Verification of the new cancellation test

Temporarily removing the durable GET-state proof after a failed DELETE made the
new unapplied-cancellation test fail: the wizard RAM was incorrectly cleared while
the operation remained PENDING. The exact source was restored before the final
118-test run. `git diff --exit-code` confirmed no permanent runtime edits; this
review adds tests and this report only.

## Static boundaries examined

Core derives the factual question from the saved draft, not client routing fields.
Private context requires the existing service key, actor and a current lease; it
rechecks safe-stop after locking and excludes uploads, correction IDs and storage
metadata. Completion rechecks current source/lease/authority after network waits,
validates the unconfirmed interpretation and writes an immutable proposal audit.
It does not itself confirm a fact, create a case or invoke legal analysis.

The existing runner handles both preparation kinds under the same two-slot
ModelAdmission instance used by analysis consumers. Metadata-only dispatch chooses
the dedicated answer endpoint and verifies operation/draft/revision/input/answer/
question bindings before completion. Duplicate claims do not schedule another call;
cancellation and shutdown release capacity and fence late publication. No model
wait occurs in the Telegram input handler: pending/refresh/manual/cancel are short
authoritative operations.

Code Graph was used for scope before review and refreshed afterwards. Current PTB
conversation-state/concurrency documentation was fetched through Context7; the
existing actor-serial processor is retained, rather than relying on unconstrained
parallel ConversationHandler updates. No external CLI, SSH or production access
was used.
