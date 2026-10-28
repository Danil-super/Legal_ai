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

## Verification and limits

Behavioral tests reproduce heavy-module collisions and verify deterministic
distribution, new-file inclusion, canonical ordering, complete unique coverage,
fail-closed manifest handling and real pytest exit propagation. Existing
independent tests retain the aggregate CI success-only gate.

After the reference refresh the checkout's estimated loads are
149.196–149.244 seconds across six workers (359 files). Collection, machine load and future
changes affect actual runtime; these estimates do not demonstrate that the
full suite passes within 180 seconds. A fresh complete PostgreSQL/MinIO run and
GitHub CI remain required before release. No workflow gate, deadline, fixture,
test skip or application policy changes with this decision.
