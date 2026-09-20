# Security and deployment review — 2026-09-20

## Deployment that appeared stuck

CI run 35513832052 for 5f474d3d1b9d435d8e8940946b1432bc9023a9d2
completed production deployment successfully at approximately 14:01 UTC. The job
started at 13:39:37 UTC. Its MinIO `go build -p 1` step took 1183.5 seconds;
Telegram became Healthy at 14:01:06 UTC. The dominant delay was source compilation
on the VPS, not a hanging legal analysis. The selected deployment did not include
the Hermes analysis profile. Readiness of this stack is not end-to-end LLM evidence.

Do not cancel a live deployment merely because a compilation step is quiet. The
next infrastructure task is to build/test versioned images off the constrained VPS
and deploy reviewed immutable digests, with registry permissions and rollback planned
explicitly. This change does not introduce a registry, replace the pinned MinIO
security release or change installed root-owned deployment scripts.

## Findings corrected

1. DOCX DTD policy used ASCII byte searches. Small UTF-16 synthetic inputs bypassed
   the check and internal entities were expanded. This demonstrates a parser policy
   bypass, not arbitrary file read or remote execution. Parser-level defusedxml
   prohibitions now apply independently of XML encoding.
2. ZIP parts could be duplicated or ambiguous, and encryption could surface as a
   missing-parser error. Validate these before reading; preserve expansion limits.
3. Native parser stderr was included in ValueError, which the upload API returns
   as its validation message. A synthetic stderr marker was reflected before the
   fix. Now the error is bounded and generic; no document excerpt is returned.
4. The Hermes HTTP body was buffered before the inner message length check. Use
   bounded streaming with a total timeout. Enforce no redirects per request and
   reject compressed envelopes before decompression. Content-Length is not trusted
   as the only bound.
5. Model JSON accepted duplicate keys and non-standard numeric values. Reject
   ambiguous objects and non-finite numbers; sanitize decoding failures.

The DOCX-only baseline reproduced 16 failing assertions out of 25 targeted cases on
the old parser. After correction all 25 pass locally. This includes hardening cases
as well as the demonstrated bypass; it is not a count of 16 distinct vulnerabilities.
New tests use synthetic data and never contact production endpoints.

## Added checks

- Reusable security CI: Bandit (selected medium/high severity, high confidence),
  Gitleaks current working tree with redacted output, and four actual container
  build/import checks. New checks gate deploy; CodeQL remains separately conditional.
- Checksum-pinned scanners and actions; source/secret snippets are not published in
  logs or artifacts. CI prints only rule/location summaries and fails on scan errors.
- Regression assertions ensure deployment cannot silently omit the security gate.
- Shared non-cancelling deployment/rollback queue; existing server lock retained.

## Not established by this review

No claim of exhaustive security, absence of all vulnerabilities, legal-answer
correctness or live Hermes compatibility is made. The remaining high-priority work
is native PDF resource isolation, an explicitly authorized historical secret review,
container/OS vulnerability scanning, and a synthetic live end-to-end analysis under
the actual approved corpus and risk policy. Rotation is required for any real
credential found exposed; merely deleting its current file is insufficient.

Use `/analysis_status` as clinic owner after deployment to distinguish intake-only
mode, missing policy/corpus and unavailable orchestration. Do not paste app.env,
provider keys, patient records or raw native-tool error output into issue reports.
