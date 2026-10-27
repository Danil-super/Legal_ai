# Optional intake materials do not block the factual summary

27 October 2026. Narrow AIF05 slice under SPEC-ai-first-resolution.md. The other
material-question and multi-fact interpretation work remains separate.

## Decision

Missing `caseMaterialsStatus` no longer selects the upload step. This is transport
metadata, not an unknown fact about the incident: successful upload/delete receipts
already set the state. Summary presentation and fact mapping represent the absence
of a confirmed attachment as NOT_ATTACHED. Explicit ATTACHED is preserved; invalid
explicit values still require correction. Required medical/legal facts, unknown
answers, risk routing and Core missing-fact checks remain enforced.

The final summary has one optional «Добавить файл» control before explicit summary
consent. It opens the existing durable MATERIALS step with the existing privacy,
file-size/type, receipt, optimistic-revision and transient-message protections.
A draft saved at MATERIALS without a completed NOT_ATTACHED choice resumes that
step. Generic resume skips an already completed NOT_ATTACHED checkpoint. Choosing
«Добавить файл» explicitly reopens that choice by removing only NOT_ATTACHED, so
the selected upload workflow survives restart; ATTACHED is never removed this way.

Before submission, ATTACHED requires a live metadata-only list from the existing
owned draft-material endpoint. Empty metadata yields a clear upload/skip choice;
unavailable or invalid metadata yields a retryable summary without submission.
Neither condition silently changes ATTACHED to a factual negative. A closed draft
after a lost successful submission may replay only after obtaining its real,
authorized typed workflow receipt with the same workflow ID and v2 intake schema.
The existing idempotent submit still validates the exact payload.

No file content is newly sent to a model. No Core endpoint, storage, permission,
personal-data category, policy or external service is added.

## Verification

New tests first reproduced seven failures. They now cover the real PTB path from
description to summary and optional upload/skip, existing MATERIALS resume, absent
metadata, valid ATTACHED preservation and closed-draft receipt recovery. Adjacent
wizard/safety/single-summary/upload regression suites pass. The original AIF01
optional-upload reproduction is checked directly, not weakened or marked xfail.
Final focused run: 161 passed, zero skips; original reproduction: 1 passed.
Ruff and gateway mypy (53 source files) pass. Reverting the optional-upload
condition makes its regression fail; the correct condition was restored.
This slice does not establish complete AI-first or legal-release readiness.
