# Pre-report lifecycle, readers and transport checkpoint

Date: 2026-10-15. This is not a release or a complete Hermes dialogue.
Implementation worktree: `/tmp/dental-pre-report-clarification-20261015`.
The original dirty workspace and production server were not modified.

## Completed and verified

- PRC2 runtime:d2bd5da/9bf21b2. Durable proposal reservations, exclusive completion
  tokens, current actor/creator/clinic/expiry checks, replay validation and manual
  fallback are independent from confirmed user facts. No model call or HTTP
  preparation producer is enabled by these primitives.
- Two actionable independent-review findings were reproduced before fixing:
  retained-row replay binding and retention/completion lock ordering. A real
  two-connection expiry test prevents both stale completion and40P01. Access is
  repeated after locks, including entitlement wall-clock expiry. Reviewer repeats
  46 focused tests on its own fresh PostgreSQL; no Required/Critical remains.
- Immutable9bf21b2 full suite:2791 passed,0 skipped,298 existing PTB warnings,
  184.93s, fresh PostgreSQL/MinIO. Ruff and all-three-service mypy142 files pass.
- PRC3 transport:8570ae1/b34c252/d30b8a9. Strict report-free receipts coexist with
  unchanged legacy reports; API GET and matching submission replay do not fabricate
  CaseReport/PDF. Expired or changed-input replay is denied. Reviewer has no
  Required/Critical; its78 pure/gateway passes do not replace the author's actual
  PostgreSQL API tests.
- PRC4:d36d602/8798333. Separate V2 cache namespace/resource checks, typed queue
  statuses, restart reconciliation without another model call, bounded invalid-cache
  terminal failure and notifier ACK. Intermediate results have no PDF. Reviewer
  repeats81 tests on its own PostgreSQL without skips and checks READY callbacks,
  PDF, notifier and same-clinic creator/resource/purged-content negatives.
- PRC5 transport:ab5c884. Exact opt-in version header, preserved auth/lease/job key,
  context/case fingerprint checks and no silent legacy downgrade. Existing models,
  providers, high reasoning and time budgets remain unchanged.26 new/43 combined
  agent tests pass. Independent transport/packaging review has no Required/Critical:
  60 focused passes plus242 pure cross-layer passes; PostgreSQL skips in that
  review are explicitly not integration evidence. Its separate typed READY API
  roundtrip and in-memory fingerprint-guard mutation also pass their expected checks.
- PRC5 worker follow-up:ebb5520 forwards only the stored job's version and checks
  the refreshed current lease before I/O. Six new tests cover legacy/V2 transport,
  stale token, expiry, terminal state and another connection replacing an already
  loaded ORM row. Independent fresh PostgreSQL42 tests pass, no Required/Critical.
  Its HTTP harness proves no job lock/runtime transaction survives into I/O; the
  removed-refresh mutation is killed by the race regression. One author focused
  repeat reused a populated DB and hit eight duplicate fixed-ID fixtures; those
  failures are not counted as passing evidence. Fresh full verification follows.

## Full-suite regression found and fixed before release

The d30b8a9 full run had2874 passed and2 failed, not a green run. Actual container
copy-set tests exposed imports of backend api_contracts/analysis_jobs from Telegram.
Do not discard or weaken those failures.1a5c74e/6eb1d2c relocate shared wire types
to dependency-light shared contracts with identity-preserving old re-exports.
Gateway no longer imports Core API, queue storage or ORM models. The Dockerfile
copies required pure contracts/hash dependencies, and both build-time import and
the isolated copy-set test explicitly import the optional analysis runtime.

Five packaging tests now pass, including deliberate missing-module negatives.
The actual Docker image `dental-prc-gateway:20261015-reader-check` built successfully
as4b82cb29aa6c and imported both runtime entry points as the unprivileged app user.
The112 focused cross-service tests and all-three-service mypy/Ruff pass.
Compose config passes with synthetic test-only environment values, not server secrets.
Fresh immutable6eb1d2c full PostgreSQL/MinIO rerun:2904 passed,0 skipped,
298 existing PTB warnings,199.25s. This closes the container import regression;
no claim that the new user dialogue exists follows from this result.

Final code checkpoint ebb5520:2910 passed,0 skipped,298 existing PTB warnings,
194.05s, on another freshly migrated PostgreSQL database with MinIO integration.
All-three-service mypy142 source files and Ruff pass. No tests were disabled,
deleted or marked skipped to obtain this result. Independent worker delta review
also passes42 tests on its own fresh PostgreSQL. The final documentation-only
commit does not change the tested code tree. All full runs use explicit source
PYTHONPATH, not the editable installation in the owner's original dirty workspace.

## Remaining release boundaries

The new Core preparation writers/retention and pre-context limits, source-bound
recovery/buttons, report-free intake producer, Hermes initial/free-answer
interpretation, explicit proposal acceptance and shared Telegram clarification form
are still PRC6–11 work. Worker version propagation is complete in the PRC5 follow-up.
No new producer is enabled, no production migration/deploy or provider/model
configuration change was made. Normative/reference approvals remain untouched.
This checkpoint does not establish complete legal readiness or both new user flows.
See ADR0091 for exact wire/storage/rollout contracts and official framework sources.
