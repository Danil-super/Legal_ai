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
Its initial 345 file estimates sum setup/call/teardown testcase durations from
the six complete local R2 JUnit reports at source revision
`f45b8e3` (4,897 cases). That calibration run had three failing assertions later
addressed separately; its timings are measurements, not evidence of a passing
release gate. Positive estimates have a 0.05-second floor, and files without
history receive a positive one-second default. Stale entries never select tests
or exclude files. Missing manifests, duplicate JSON keys, unsupported fields or
versions, unsafe paths and nonpositive/nonfinite durations fail selection.

The manifest stores relative source filenames and timing numbers only; no case
content, patient data, database credentials or runtime artifacts are included.
Refresh estimates from a complete integration run when module costs change.

## Verification and limits

Behavioral tests reproduce heavy-module collisions and verify deterministic
distribution, new-file inclusion, canonical ordering, complete unique coverage,
fail-closed manifest handling and real pytest exit propagation. Existing
independent tests retain the aggregate CI success-only gate.

At implementation time the current checkout's estimated loads are
133.45–133.50 seconds across six workers. Collection, machine load and future
changes affect actual runtime; these estimates do not demonstrate that the
full suite passes within 180 seconds. A fresh complete PostgreSQL/MinIO run and
GitHub CI remain required before release. No workflow gate, deadline, fixture,
test skip or application policy changes with this decision.
