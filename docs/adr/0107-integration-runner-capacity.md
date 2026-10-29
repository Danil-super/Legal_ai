# ADR 0107: Integration runner capacity with unchanged acceptance gates

29 October 2026. PR116's complete integration suite exceeded the 180-second
process deadline in two six-worker runs, without an assertion failure. Four
workers passed in each run; the failed workers reached 100%/66% and 96%/50%.
Rebalancing average costs alone did not provide reliable capacity.

Matched modules in the second run took 0.78 to 1.33 times their first-run duration
when aggregated by worker. These observations are consistent with runner or
order-dependent variance; they do not establish a single cause. Local source
tests and native activity are not credited with explaining Core database costs.

Using the larger observed duration from both runs and each existing full estimate
gives approximately 1,025 seconds for all 386 test files. Largest-first scheduling
would assign approximately 171 seconds to each of six workers, plus up to 8.3
seconds of observed collection/startup: less than one second of reserve. Eight
workers instead receive approximately 128 seconds each, or 136.5 seconds including
that startup allowance. This is a capacity forecast, not proof that CI passed.

The integration matrix therefore expands to eight isolated workers. Every worker
keeps the existing 180-second process deadline. Test discovery, canonical ordering,
PostgreSQL/MinIO isolation, native dependencies, fail-fast=false, all security and
quality checks, and the required aggregate/deployment success gates remain intact.
No tests, filters, skips, service definitions, secrets, or legal/runtime policies change.
Timing metadata never falls below an existing full estimate; incomplete module
observations cannot replace that estimate. A guard verifies that the configured
eight-worker partition includes every discovered repository test file exactly once.

GitHub documents that each matrix value creates a separate job and that `needs`
gates downstream work on required job success. The existing always-run aggregate
still accepts only the matrix result `success`:
[matrix jobs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations),
[failure handling](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
[required jobs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs).

Evidence from runs 40890736445 and 40893427507 is retained in the operator cache
as `ci-runtime-second-run-independent.json` and
`ci-runtime-shard-capacity-independent.json`. Release still requires a successful
complete CI run; passing local tests or the scheduling forecast cannot replace it.
