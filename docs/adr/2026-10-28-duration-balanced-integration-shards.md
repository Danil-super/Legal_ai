# Duration-balanced integration test files

Date: 2026-10-28

## Problem

Round-robin assignment by file count puts slow PostgreSQL modules together when
new test files shift their positions. A complete local run reached the existing
180-second pytest deadline in four of six parts. Increasing that deadline or
omitting tests would hide the regression in the verification process.

## Decision

Keep the six isolated workers, their 180-second process deadline, all configured
pytest roots and both filename patterns. Discover every test file first. Assign
whole files in descending estimated duration to the currently lightest worker;
canonical file position and worker index make ties deterministic. Restore the
configured root/file order inside each worker so Core fixtures are collected
before cross-service imports. Replace the selector process with pytest exactly
as before, preserving pytest failures and the outer timeout.

`ops/ci/integration_file_weights.json` is a required, versioned scheduling hint.
Estimates sum setup/call/teardown testcase durations from complete local JUnit
reports. Initial calibration used R2 at `f45b8e3` (345 files, 4,897 cases).
The 2026-10-28 refresh uses all six R4 reports at `3ab0829` (346 files,
4,951 cases), with the migration module measured again after moving its
destructive roundtrip into its own disposable database. R4 had one migration
assertion failure and one worker exceeded the outer deadline after reporting
all cases; neither the timings nor that run constitute a passing release gate.
Positive estimates have a 0.05-second floor, and files without
history receive a positive one-second default. Stale entries never select tests
or exclude files. Missing manifests, duplicate JSON keys, unsupported fields or
versions, unsafe paths and nonpositive/nonfinite durations fail selection.

The manifest stores relative source filenames and timing numbers only; no case
content, patient data, database credentials or runtime artifacts are included.
Refresh estimates from a complete integration run when module costs change.

A subsequent reference-analogy calibration at `308b936` ran all nine new
reference Core/Agent modules sequentially against disposable PostgreSQL:
77 tests passed without skips in70.39seconds. Their complete JUnit testcase
durations replace the one-second defaults, including28.146seconds for selection,
18.003seconds for freshness and14.182seconds for late ordinary submission waits.
The test database was separate from other concurrent verification runs and was
deleted afterward. These are scheduling measurements, not a full-suite gate.
A second complete PostgreSQL/Compose run after `0a14710` and `8328aea` passed
22 tests without skips in22.36seconds, adding19.924seconds for the five late
work-product write/replay waits and1.041seconds for flag packaging checks.
The five-case worker/model-boundary/Core/human-review module at `2306815`
passed without skips in9.74seconds; its8.938seconds of testcase durations
replace the older four-case estimate. Only these measured modules are refreshed;
the remaining estimates retain the R4 calibration.

The first GitHub run at `fa4596f` (run40718956883) exposed a mismatch between
local estimates and actual isolated runner costs. Shards0,1,4 completed in
158.32,117.32,140.38seconds; shards3,5 completed in112.51,126.48seconds with
assertion failures, and shard2 reached the unchanged180-second timeout at64%.
These failures are not passing integration evidence. The slow shard continued
completing tests, with no single observed inter-test gap over4.344seconds.

Refresh scheduling hints from these six job logs: timestamps between completed
test-result lines approximate setup/call/teardown costs per file. Start after
pytest collection, excluding runner/MinIO setup. Fixture teardown at a file
boundary can be attributed to the next file; these remain scheduling hints,
not profiler measurements. All338 fully observed files receive measured hints.
The partially observed `test_answer_http_postgres_flow.py` keeps the maximum of
its old18.371seconds and its4.344-second observed prefix; twenty unobserved
files retain their earlier positive estimates. Do not extrapolate a completed
run from the timed-out prefix. The corresponding metadata-only timing artifact
records source run/job IDs, case counts, zero observed skips and completion
status; it contains no test input or credentials.

The next complete run at `623d157` (40721837785) passed all5073 PostgreSQL/MinIO
tests without skips; the six pytest processes took96.61–153.38seconds. This is
the verified baseline before the subsequent source-bound reference-review fix.
That fix adds a measured39.749-second Core scheduling hint: its complete
35-case PostgreSQL JUnit run passed without skips in40.59seconds, followed by
one additional zero-I/O legacy-receipt compatibility test. Nine new Gateway
snapshot tests passed in the complete unit run and sum0.080seconds of JUnit
testcase durations. Four modified approval-fixture modules were measured together
without skips (30 cases,28.72seconds); their hints are refreshed from that full
JUnit report. These added measurements do not reuse partial/failed runs as success.

## Verification and limits

Behavioral tests reproduce heavy-module collisions and verify deterministic
distribution, new-file inclusion, canonical ordering, complete unique coverage,
fail-closed manifest handling and real pytest exit propagation. Existing
independent tests retain the aggregate CI success-only gate.

With both new Core/Gateway snapshot modules the combined checkout's estimated
loads are145.585–145.633seconds across six workers (361 files).
Collection, machine load and future
changes affect actual runtime; these estimates do not demonstrate that the
full suite passes within 180 seconds. A fresh complete PostgreSQL/MinIO run and
GitHub CI remain required before release. No workflow gate, deadline, fixture,
test skip or application policy changes with this decision.
