# ADR 0036: Bounded document/model inputs and required security CI

Date: 2026-09-20
Status: Accepted

## Decision

Use defusedxml with DTD, entities and external references forbidden at parser level
for WordprocessingML. ASCII byte searches are not an encoding-independent XML policy.
Keep ZIP entry/count/expansion limits and add duplicate/ambiguous path rejection,
encryption rejection and a bounded part read. Reject control characters in upload
filenames. Do not return native parser stderr to the upload API. Declare defusedxml
as a direct dependency; version 0.7.1 is already pinned in requirements.lock.

Increment the DOCX parser identifier to docx-wordprocessingml.v2. Existing approved
versions, raw hashes, normalized text and reports are not rewritten. Any future
reprocessing must use the existing version/review workflow, never silently replace
approved content. Ordinary UTF-8/UTF-16 DOCX text extraction remains supported.

Bound each Hermes HTTP envelope to 1,000,000 bytes and total request time to the
existing endpoint timeout. Reject redirects per request even with an injected client.
Request identity encoding and reject compressed responses before decoding. Check both
Content-Length and actual streamed bytes. Reject duplicate JSON keys, non-standard
numeric constants and numeric overflow; malformed/deep JSON becomes a sanitized
protocol error. Keep existing prompt/content limits and exception categories. This
is a transport/parser change, not a change to approved evidence, verifier policy,
authorization, risk or patient sending. Both Hermes services must return identity
encoding; test their actual integration before a real-data pilot.

Call security.yml as a required CI job. It runs checksum-pinned Gitleaks CLI on the
current checkout (not historical Git objects), Bandit on production Python with
medium/high severity and high confidence, and real Docker image builds/imports for
Core, Telegram, orchestrator and watcher. Scanner output exposes only locations and
rule IDs, not matched content. Invalid/incomplete reports or non-zero scanners fail
the gate. No production credentials or model calls are used. Deploy requires this
job, quality and PostgreSQL/MinIO integration success; the existing optional CodeQL
condition remains unchanged.

Deployment and explicit rollback share a non-cancelling concurrency group. The
server-side lock is retained. No installed root-owned script or server environment
is modified by this change. A pending run can be superseded in GitHub's queue; this
is not a guarantee that every intermediate commit is deployed.

## Validation and limits

Synthetic regression tests cover XML encodings, duplicate ZIP parts, encrypted
archives, unsafe names, error privacy, response-size/header disagreement, slow
streams, redirect prevention and strict JSON. Container imports run as non-root,
read-only, without network or Linux capabilities. They do not prove model readiness.

This is not a full penetration test, historical secret audit or OS/container CVE
scan. Native PDF tooling still needs dedicated process memory/output isolation for
hostile high-volume uploads. No finding here proves a production compromise.
