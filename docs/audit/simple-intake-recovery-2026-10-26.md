# Simplified intake and amount recovery: verification record

## Delivered scope

One visible new-case action starts a narrative-first intake. Old guided drafts
remain resumable. Multiple-choice cards keep their message identity while choices
are persisted and edited in place; only «Продолжить →» advances. Explicit add/remove
callbacks make retries idempotent. Unmatched save receipts cannot update checkmarks.

The incident was a failed MODEL_PROTOCOL_INVALID amount interpretation, omitted
from discovery, with no staged amount. Old recovery controls could not rediscover
the failed operation. Recovery now independently reads authorized current facts
and requires successful empty discovery; a failed recovery navigation offers
draft reopen/menu instead of another broken operation control. Lost stage replies,
other active drafts, revoked access and discovery failures retain their fences.

Literal ruble scalars use the existing source/privacy/range validation without
a model request. These are provisional values until final summary consent.
No model/profile, risk, tenant, approval, schema or patient-delivery changes.
Contracts: ADR0098/0099.

## Local evidence

- Snapshot880b32f:4398 tests pass, zero failures/errors/skips or duplicate cases,
  across six fresh PostgreSQL databases and separate MinIO buckets.
- Snapshotf388d1e adds only a targeted filelock lockfile update and two consumer
  tests. Five shards pass; initial shard4 has two short parser-process timing
  failures while three image builds run. No limits or assertions were relaxed.
  All32 parser tests pass alone. Replacement shard4 passes929 tests on a fresh
  database at7b66fd5, whose only difference fromf388d1e is documentation layout.
  The verified combined partition is4400 tests, zero failures/errors/skips or
  duplicate cases. Original failing artifacts are retained, not overwritten.
- Independent review reproduced the recovery failure, then verified its fix;
  adversarial/mutation checks cover discovery, ownership and persistence receipts.
  Final runtime and dependency delta reviews have no Required findings.
- Ruff and180-source-file mypy pass; Bandit has zero qualifying findings.
  Production/analysis Compose validates. Core, Agent and Gateway production images
  build and import offline as non-root with read-only filesystems.
- Existing pip-audit discovered three new filelock advisories on26October.
  The normal pip-compile single-package update changes only3.32.5→4.2.1 and its
  two hashes; CacheControl's declared range permits it. FileLock's consumer API
  remains compatible. A preexisting-lock-file regression fails before and passes
  after the update; cache read/write/delete and pip check pass. Fresh audit: zero
  known vulnerabilities. No other dependency pin or advisory suppression changes.

Maintainer references: [4.2.1 release](https://github.com/tox-dev/filelock/releases/tag/4.2.1),
[security advisory](https://github.com/tox-dev/filelock/security/advisories/GHSA-v4x8-3wr5-3wq8).

## Release boundary

Owner authorized release only after successful checks and independent review.
GitHub CI and production verification remain separate gates. These synthetic tests
do not establish correctness of every legal answer or external model availability.
Approved applicable evidence and risk/escalation checks remain mandatory.
