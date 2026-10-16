# PR100 bounded CI and atomic entry: independent review

Reviewed on 2026-10-16 in separate read-only-source worktrees. No production,
provider, credentials, approvals, database roles or external CLI were changed.
Required findings: none in the exact patches below. Approval is scoped to these
patches and does not replace successful release CI or a live smoke test.

## Complete-suite CI sharding

Source commit: `ddac409`. Independently verified immutable blobs:

| File | Blob |
| --- | --- |
| `.github/workflows/ci.yml` | `28257576d2788b0f2b0858881b450a0c1ac5e5c6` |
| `ops/ci/run_integration_shard.py` | `57f9cabde555fb32d6c34dddce68c681ecb880a7` |
| `tests/test_integration_shards.py` | `daa175d47b534c4a52c42f4101304a4da32c443c` |
| `tests/test_security_ci.py` | `35aae40d2859aa3240e929463030b1119ac92a11` |

The selector reads every configured canonical `testpaths` directory and test-file
pattern; it preserves root collection order, rejects invalid/empty/overlapping
selections, and executes pytest without `-k`, `-m` or exclusion filters. Its exec
handoff preserves pytest exit codes and the existing outer 180-second deadline.
Each matrix runner keeps PostgreSQL and MinIO integration enabled. The old
required `postgres-integration` name is retained as an always-run aggregate;
only `success` permits it to pass. Deploy dependencies and their success checks
are unchanged. `fail-fast: false` lets independent children finish, without
making a child failure acceptable. This matches official GitHub documentation
for [dependency and always semantics](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs)
and [matrix failure handling](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations).

Actual canonical and three explicit-shard collect-only runs all exited zero:
282 test files; 3,985 collected cases = 1,340 + 1,263 + 1,382 across 94 files per
shard. File/function counters including parametrization multiplicities matched
the canonical collection. Five pre-existing UUID-generated parameter display IDs
change between processes, so exact opaque node-ID string equality is not claimed.
These counts describe the reviewed source before addition of this review's ten
controls and later activation tests; the selector discovers those additions too.

Independent subprocess controls executed real passing, failing and collection-
error pytest children (exit 0/1/2), exercised the actual aggregate shell command
for success/failure/cancelled/skipped/empty/unknown outcomes, and covered pattern
overlap and empty-shard refusal. On committed source `ddac409`, all ten independent
controls plus 27 author CI/security tests passed: **37 passed, zero skipped** in
0.91 seconds. Ruff passed for the independent test file. No live database was
used for this review.

The original failed CI job `117104522028` in run `38939871120` was independently
read through GitHub MCP: its 180-second command began at 17:33:14.987 UTC; the
last displayed pytest case passed at 71% at 17:36:14.899; exit 124 followed at
17:36:15.033. There were no explicit pytest FAILED/collection-error/failure-summary
markers. This establishes a deadline termination, not a complete-suite pass.
The Gitleaks remediation `7eab3ae` was also reviewed: only unambiguous dependency
version formatting and synthetic strings changed, with no scanner exception or
weakened assertion.

## Atomic production-entry switch

Separately reviewed `78dd1aa`, factory blob
`39dbc70fc1009d73808c8e14e061081577959a94`. `PRE_REPORT_INTAKE_ENABLED` accepts
only exact `0` or `1`; absence means off; invalid values fail before reading the
token or constructing/polling an application. `1` enables both initial narrative
understanding and subsequent factual-answer interpretation together. Explicit
factory defaults remain false; this production entry offers no initial-only
deployment mode. It does not enable personal features or alter provider, risk,
source-approval or acceptance gates.

Independently ran the actual production-entry tests in an isolated worktree:
**15 passed, zero skipped**, 0.35 seconds. This approves the exact entry source
before baseline-pin advancement, not a later Compose change or actual server
activation. Default-off wiring is not the same as an approved live activation;
the final release record must state which deployment value was actually applied.

Follow-up Compose review: `dc738ed`, base Compose blob
`9ace2c7b92db76cf03cb4f158d29ed9309e1ffa9`, production-template blob
`f023c276f62255b1e0207e461f3f53f9a594e107`. Approved before baseline-pin advancement.
Only the clinic Gateway receives the variable; Compose's absent/empty default is
`0`, explicit `1` reaches the atomic entry, and invalid nonempty input is preserved
for strict startup refusal. The production example remains `0`. Actual resolution
of base, production, and Hermes overlays plus entry tests: **31 passed, zero
skipped**, 1.16 seconds. No provider, personal feature or other service setting was
changed. This additional source review did not activate or access a server.
