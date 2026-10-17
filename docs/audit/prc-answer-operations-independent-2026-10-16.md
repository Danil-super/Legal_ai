# Independent review: subsequent-answer reservation and consent

Reviewed source snapshot: `d1a6ea9` (2026-10-16). Review branch:
`review/prc-answer-operations-20261016`. No production, SSH, real provider,
external CLI or real patient documents were used.

## Verdict and scope

No required correctness/security finding was reproduced in the reviewed reservation,
status, explicit acceptance and cancellation boundaries. This is a scoped review,
not release clearance for the complete Telegram flow, live model quality, legal
recommendations or the as-yet-unreviewed subsequent lifecycle/API composition.

The reviewer wrote the earlier pure answer contracts; review independence here is
for the root-authored durable operations/acceptance, not a claim of independent
authorship of those pure helpers.

## Actual verification

The combined run passed **173 tests, zero skipped, zero failures, 10.35 seconds**:

- 19 new independent PostgreSQL probes;
- 20 existing reservation/consent PostgreSQL tests;
- 134 existing pure intent/wire/question/value/service tests.

The least-privilege runtime role used PostgreSQL at `127.0.0.1:55486`, disposable
database `dental_legal_test_answer_lifecycle_20261016`, after an explicit handoff
from the other reviewer. This review did not reprovision shared roles. All fixture
identities and narratives were synthetic.

JUnit artifact:
`/home/danil/.cache/dental-prc-verify.pLetxJ/answer-operations-independent-final-d1a6ea9.xml`

SHA-256: `15287f24ed758b8492477b11643994eeebf99807d6371896e41a5f4ddfa0da51`.

Ruff passed for the new test file. Existing source was not changed permanently.
An initial test run had one fixture FK setup error: the test attempted to move a
membership already referenced by an entitlement. The fixture was corrected to
create the synthetic membership without an entitlement, move it, then create the
entitlement. No application guard or database constraint was weakened.

## Independently exercised boundaries

- Two different simultaneous answers for the same question: one lease, one bounded
  conflict, without choosing whichever answer finishes last.
- Old narrative and new answer decoders reject each other's records; a wrong
  cancellation cannot alter the legitimate pending operation.
- Draft revision, content, wizard state, archive and expiry fence old answer reads
  and cancellation.
- Another creator in the same clinic cannot read/cancel the operation. Runtime RLS
  separately hides a foreign-clinic record even from a direct query without the
  application's clinic filter.
- The existing retention function scrubs the new scope's stored answer and lease.
- Four real draft-row lock waits were observed in `pg_stat_activity`. Role change,
  revocation, suspended entitlement and entitlement expiry during the wait prevent
  reservation. The expiry test proves `ends_at` is later than transaction start,
  so a transaction-start `now()` check alone would incorrectly grant access.
- Unversioned content substitution at the accepted revision invalidates both read
  and replay; a genuine later revision remains the existing consent test's valid
  replay case.
- Concurrent cancellation and acceptance cannot undo committed facts: exactly one
  outcome wins. Completed read/accept/cancel each recheck the current entitlement.

## Mutation evidence

Two temporary mutations were applied only inside the isolated review worktree:

1. Removing the accepted-revision output hash comparison made the new substitution
   test fail because an invalid cache was accepted.
2. Replacing the current-clock entitlement check with transaction-start `now()`
   made the actual lock-wait expiry test fail because reservation was allowed.

Both mutations were restored with exact patches before the final 173-test run.
`git diff --exit-code` confirmed no runtime source changes remained.

## Static review notes

Locks consistently follow draft then operation. Mutable cache bindings include
actor/tenant/draft/revision/input, the current question, actual answer digest and
the frozen reference date. Completed interpretations require the append-only
proposal audit; committed acceptance requires its separate audit. Replay precedes
the historical source-freshness check but does not bypass current authorization,
retention, request selectors or accepted-output integrity. A later legitimate edit
is returned as the current draft rather than overwritten by the historical answer.

Acceptance uses the existing canonical dependency helper, increments only the draft
revision, and has no case/fact/job/model/report creation calls. Read/cancel remain
available under safe-stop, while the existing reservation test refuses new starts
when safe-stop is already active. The future private context/lifecycle must still
fence safe-stop and authority after lock waits before any model call.

SQLAlchemy lock/identity-map behavior was checked using Context7 against the official
2.0 documentation. Code Graph provided initial scope and was refreshed after the
review test addition; its incomplete Python dependency graph was not treated as
proof that no callers exist.
