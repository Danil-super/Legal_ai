# Attorney package: actual delivery status, 2026-09-25

## Prepared and tested, not deployed

PR: https://github.com/Danil-super/Legal_ai/pull/52

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

## Deployment blocker

GitHub Actions run `36169017320` did not start its jobs. The `quality` check
annotation reports failed account payments or a spending limit requiring an
increase, and directs the account owner to Billing & plans. All jobs failed before
executing checks; this is not evidence of test or security-scan failures.

Do not bypass the agreed CI gate, merge as if checks passed, alter account billing,
or patch running containers. The owner must resolve the GitHub Actions billing
restriction. Then rerun CI for the current PR head, merge only after passing checks,
and wait for main's normal deployment. No production code or corpus rows were
changed by this work.

## Release verification after the blocker is resolved

1. Verify deployed commit, Core/gateway readiness and unchanged editor permissions.
2. Run the repaired importer on the protected 58-file server package using package
   key `garant-2026-09-25`; retry once and confirm exactly 58 rows for that package.
3. Read every group/page and download every artifact through the authenticated API;
   compare checksums, counts and MIME types. Confirm ordinary users are denied.
4. Confirm no source or version became APPROVED as a side effect of import.
5. Complete the metadata-to-version workflow before claiming all legal copies can
   be approved. Original/current publication identity and effective dates must be
   supplied or checked explicitly; do not invent them from the receipt date.

The schema is unchanged by this PR. The prior production revision `a92aa96` can
still read imported materials; rollback must preserve the original package and DB.
