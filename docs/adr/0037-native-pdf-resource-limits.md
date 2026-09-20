# ADR 0037: Native PDF process bounds and reviewed secret-scanner controls

Date: 2026-09-20
Status: Accepted

## Native parser boundary

Run clinic-upload pdfinfo/pdftotext via the system prlimit executable with hard and
soft RLIMIT_AS (512 MiB), RLIMIT_CPU (the existing 30/60 second per-tool timeout),
RLIMIT_FSIZE (1,000,000 bytes per output file), RLIMIT_CORE (zero) and RLIMIT_NOFILE
(64). Use secure temporary output files instead of unbounded pipes. Read only bounded
UTF-8 stdout on success; never return native stderr or exception diagnostic bodies.
A wall timeout terminates/reaps the direct process, including time spent blocked.

Resolve only the two server-owned tool names and prlimit through /usr/bin:/bin.
The child receives only that PATH and C locale, not application credentials, HOME,
proxy or dynamic-loader settings. Do not use preexec_fn in threaded upload processing.
Install util-linux explicitly in the Core image and verify prlimit exists at build.
Missing prerequisites fail closed; non-Linux direct installations have no unsafe fallback.

Require pdfinfo to explicitly report Encrypted: no. Its common Encrypted: yes
(permission details) format must not bypass the pre-extraction encryption gate.
Change only newly generated PDF parser metadata to pdftotext-clinic.v2. Existing
approved documents, versions, text, hashes and reports are not reprocessed or altered.

These are per-process resource limits, not a filesystem/network sandbox, per-tenant
quota, aggregate upload concurrency limit or proof against native parser vulnerabilities.
No auth, retention, rate-limit, corpus approval, risk or model settings change.

## Scanner false positives

The first full Gitleaks checkout scan reported four generic-api-key matches: the
requiredFactKeys Pydantic alias in three contract modules, and one exact nonfunctional
placeholder in production.env.example. Source inspection confirms these are not
credentials. Add rule-specific AND exceptions requiring both exact value and exact
path, extending all default rules. No file or directory is excluded wholesale.

A real Gitleaks sensitivity control first scans these same aliases/placeholders, then
adds freshly generated, never-issued high-entropy test values to every exception path.
It must ignore the former and detect the latter. No values or source excerpts are
printed. The normal checkout scan must still pass independently; neither result is
silently swallowed. A historical Git scan is a separate, not-yet-completed task.

## Verification

Tests execute actual bounded child processes to check kernel limits, environment
isolation, excessive stdout/stderr, address-space rejection, timeout cleanup and safe
errors. Real synthetic plain and encrypted PDFs (including empty user passwords) are
processed by Poppler. Core-image CI also runs these tests offline, as non-root,
read-only and with all Linux capabilities dropped. These tests do not contact any
production API or consume a model call. Full CI and deployment results are evaluated
on the committed revision, not inferred from local tests.
