# Pre-report preparation: foundation checkpoint, 15 October 2026

Status: PRC1 complete; PRC2 schema/retention checkpoint implemented, runtime
operation lifecycle NOT complete. No production deployment, schema change,
Hermes understanding activation, model/provider change or legal approval.

## Implemented

- Opt-in strict v2 intermediate results, separate from legacy reports. Only a
  context-bound READY/PASSED result carries a report; preparation carries no PDF.
- Existing-case/fact/run/report/evidence/date/policy binding. Fact digest recomputed;
  source applicability checked; constructed nested models cannot bypass validation.
- Server-built stable draft intent identity; actual restricted-role PostgreSQL
  roundtrip/duplicate/RLS tests, without a fake case/report for early understanding.
- Approved additive three-table migration and expiry scrubbing of early proposals
  in all states, including missing/archived/submitted/material-gated expired drafts.
  Unexpired draft content and non-content replay tombstones are preserved.
- Guarded transactional downgrade, locking against in-flight writes before the
  retained-data check. No destructive coercion into legacy report receipts.

Implementation worktree: /tmp/dental-pre-report-clarification-20261015;
branch fix/pre-report-case-clarification-20261015. Source checkpointbd6bb3c is
tree-identical to independently reviewed9f1c2c9 in the isolated schema worktree.
Original dirty user worktree was not changed or reset.

## Evidence

- Contract RED missing PreparationResult → GREEN. Additional fact/date and nested
  input regressions reproduced before their guards. Focused full-environment
  contract/identity/schema tests:241 passed, no skips.
- Mutation of fact-digest comparison caused its tampering regression to fail;
  restored original source before commit.
- Replacing only the test DB retention function with its predecessor caused
 12 failing expiry/state tests; expanded function restored all focused cases.
- Separate empty disposable DB: actual upgrade→downgrade→upgrade passed. Populated
  preparation storage refuses downgrade. Two-connection writer/rollback test passed.
- Initial PRC1 source37003b9 full PostgreSQL/MinIO run:2707 passed,298 warnings.
  Later schema checkpointd053206:2743 passed,1 failed. Root cause was the historical
  backfill test upgrading to future head before comparing whole-row metadata;
  now it targets c4a0c3f6e852 and keeps both strict metadata/content assertions.
  Reproduced failure in isolation, then that exact test passed.
- Final combined full-suite run on source9f1c2c9 (tree-identical tobd6bb3c):
 2745 passed,0 skipped,298 existing Telegram test warnings,163.32 seconds. Disposable
  PostgreSQL/MinIO enabled; POSTGRES_DB=dental_legal_test_prc_final. Command:
  `python -m pytest -q --tb=short` with the approved source PYTHONPATH/project venv.
  Ruff,140-source mypy and Compose configuration checks pass. No threshold lowered,
  skipped/removed test or weakened metadata assertion.
- Two isolated independent reviews: contract review found two actionable bypasses,
  both fixed and re-reviewed; schema review suggested the implemented rollback lock.
  Follow-ups report no remaining Required/Critical issues in their limited scopes.
  Optional external-model review offered; no external CLI invoked.

## Still required before this feature is usable

Finish PRC2 reservation/completion/replay safety, then compatible receipt/job/
orchestration readers, report-free producers and source-bound recovery, protected
Hermes understanding with explicit confirmation of proposed facts, and the shared
pre-report form. Verify both Telegram entry branches, urgent routing, UNKNOWN,
restart/edit/cancel/repeated callbacks, then reviewed CI and approved live release.

These tests do not certify the new dialogue end-to-end, current production health,
legal correctness of every possible case, or unrestricted release readiness.
