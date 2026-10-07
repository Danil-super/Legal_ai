# Local reference-case audit and comparison

Run with the project's installed Python environment from the repository root:

```bash
mkdir -m 700 /tmp/reference-review-example
python -m legal_core.reference_evaluation_runner audit /absolute/path/cases.xlsx \
  --output /tmp/reference-review-example/readiness.json
```

The XLSX source remains unchanged. The report lists every case's row hash and
missing prerequisites without scenarios or candidate answers. An assistant-ready
label and a workbook edition date never count as lawyer approval or case date.

After de-identification, contribute cases through the existing reference workspace
and have a distinct authorized reviewer approve them there. The operator can
prepare a private `reference-review-manifest.v1` JSON using
`ReferenceReviewManifest` / `ReferenceReviewReceipt` fields. Set
`workbookSha256` and each `rowSha256` from the audit, record exact workspace UUID,
version, approved status, contributor/reviewer membership UUIDs, reviewed scenario
SHA-256, privacy review, `asOfDate`, `groupKey`, and `expectedRoute`.

For HUMAN_ESCALATION and INTERNAL_DRAFT, record the lawyer's `expectedRisk` and
`expectedPolicyVersion`; INTERNAL_DRAFT additionally requires
`requiredFragmentIds`. `requiredLegalConclusionCount` records the expected
minimum number of conclusions. These local receipts cannot approve legal sources
or grant access. Keep them 0600 and rerun the audit with:

```bash
python -m legal_core.reference_evaluation_runner audit /absolute/path/cases.xlsx \
  --review-manifest /absolute/private/path/review-manifest.json \
  --output /tmp/reference-review-example/reviewed-readiness.json
```

Use an already reviewed case and the existing Legal Core analysis flow for an
authorized controlled run. Export the granted `ReferenceEvaluationDetail` and
`CanonicalReport` into a private comparison input with keys `reference`, `receipt`
and `report`. Bind `receipt.analysisCaseId` to the actual report case UUID and
`receipt.factSnapshotSha256` to the reviewed facts. Supply `report: null` if no
analysis was run. This CLI does not create cases or call providers:

```bash
python -m legal_core.reference_evaluation_runner compare \
  /absolute/private/path/comparison-input.json \
  --output /tmp/reference-review-example/comparison.json
```

PASS checks route, risk/policy, case date, evidence IDs/effective dates and
conclusion citation coverage. It does not assess the legal meaning of prose;
`legalCorrectness` explicitly requires lawyer output review. BLOCKED and
UNAVAILABLE are never counted as correct legal answers. Early-triage queue
delivery should be tested separately with the escalation API/notification tests.

Do not put workbook rows, private manifests, API exports or audit output in git,
fixtures, application logs or provider requests. The CLI does not manage a new
retention store; the operator owns and removes these local files when finished.
