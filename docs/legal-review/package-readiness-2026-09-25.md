# Attorney package: actual delivery status, 2026-09-25

## Deployed and imported; legal-version preparation remains incomplete

PR: https://github.com/Danil-super/Legal_ai/pull/52

Production revision: `2cd32d643fb2680c0c45d41c6407120ea6b1d66d`.
Successful CI and normal automatic deployment:
https://github.com/Danil-super/Legal_ai/actions/runs/36175546190

All 58 files are now stored in the production review inbox under package key
`garant-2026-09-25`. They are grouped and downloadable by the owner and both
configured legal editors. This does **not** complete preparation of the new legal
versions or make the 58 files approval-ready evidence.

The actual 58-file package was imported twice in an isolated PostgreSQL database;
the second run did not duplicate records. All 58 stored artifacts were downloaded
through the authenticated editor API and checked against their stored SHA-256.
No artifact content is included in git or in this report. This is byte integrity
and API delivery verification, not a visual examination or legal approval.

| Subject | Files |
| --- | ---: |
| Medical activity and patient rights | 22 |
| Courts, expertise and legal assistance | 10 |
| Labour and professional qualifications | 7 |
| Personal data and information | 4 |
| Licensing and regulatory supervision | 3 |
| Codes and general law | 5 |
| Clinical reference PDFs (not normative approval candidates) | 7 |
| Total | 58 |

Groups derive from supplied filenames and file kind, not from inferred legal
applicability. Every original remains accessible; classification does not hide a
file or alter its approval status. Canonical metadata preparation is still needed
for legal copies without a complete legal version. The grouped inbox is not a
batch-approval implementation.

## Prior deployment blocker — resolved

GitHub Actions run `36169017320` did not start its jobs. The `quality` check
annotation reports failed account payments or a spending limit requiring an
increase, and directs the account owner to Billing & plans. All jobs failed before
executing checks; this is not evidence of test or security-scan failures.

After the owner made the repository public, rerun `36169558989` completed
successfully. PR #52 was then merged and main passed its own checks before the
normal deployment. No billing settings, CI gates or running application source
were manually changed. CodeQL remained disabled/skipped by the existing setting;
Bandit, Gitleaks, dependency vulnerability audit and container import checks passed.

## Production release verification

- Deployed commit and last-successful-revision marker match; Core and gateway healthy.
- Two importer executions returned 58 items; the database contains exactly 58
  materials in `METADATA_REQUIRED`, with no duplicate rows from the retry.
- All 58 authenticated artifact downloads passed SHA-256, byte-count and MIME
  checks: 69,305,904 bytes total. The checksum manifest matches both the original
  local package and the protected host package. Manifest SHA-256 (sorted raw hashes,
  one per newline): `d3d1513b9b269ff0d3f7a4cc0ba2e2cc7e2296688b764ed4b21aeb673c0f98ca`.
- Owner and both configured editors receive HTTP 200. Missing gateway credentials
  and an unknown user receive HTTP 403. No permissions were changed.
- The live gateway client rendered all 15 list/group pages containing all 58
  materials; callback byte limits and back navigation passed. Longest message:
  2,538 UTF-16 units. The largest file (13,342,914 bytes) also passed download and
  checksum verification through the gateway's actual client code.
- Read-only checks sent no Telegram messages. They do not substitute for a visual
  inspection of every original or a manual click-through in the Telegram app.
- Metadata requests in this sequential loop: median 11.5 ms, maximum 113.4 ms.
  These are local Core/API measurements, not Telegram latency or a load benchmark.
- Legal approval counts were unchanged: 10 existing `REVIEW_REQUIRED` versions,
  five `DRAFT` sources, no approved versions. Import created no legal versions.
- Local main tests: 960 passed, 82 skipped, 126 warnings. Separate CI PostgreSQL/
  MinIO integration job: 436 passed, 9 skipped. Lint/type/security gates passed.

The deployment spent substantial time compiling MinIO on the 2 GiB VPS. During
the build, available memory briefly fell to roughly 140 MiB and one vmstat sample
showed 84% I/O wait. Core and gateway remained healthy in the checks performed;
neither reported an OOM kill. This is a deployment-performance follow-up, not
evidence that Telegram responsiveness under load has been benchmarked.

## Remaining work

Complete the metadata-to-version workflow before claiming all legal copies can
be approved. Original/current publication identity and effective dates must be
supplied or checked explicitly; do not invent them from the receipt date. The
seven clinical PDFs are separately classified reference materials. No automatic
or batch approval was performed or implemented by this increment.

The schema is unchanged by this PR. The prior production revision `a92aa96` can
still read imported materials; rollback must preserve the original package and DB.
