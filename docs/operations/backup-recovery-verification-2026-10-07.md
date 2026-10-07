# PostgreSQL backup recovery verification — 2026-10-07

Scope: technical recoverability audit, not production migration, document approval,
application/MinIO recovery, or an offsite-disaster-recovery claim. Only aggregate
metadata is recorded here; archive/error-row contents remain private.

## Artifact and distinct outcomes

The tested archive is the root-owned `before-import.dump` in
`/var/lib/dental-legal-ai/corpus-preparation-20261007.PVrfdW/`, mode `0600`,
49,954,949 bytes. Its SHA-256 before and after verification is
`ed44cb4f6fd9be6aa128d9f539da1c6118f93c8a21e1aba2a4f759ce88259006`.
PostgreSQL was 16.12; the archive schema revision is `a8d4e2f6b901`.

- Standard single-transaction restore of this **historical** archive: **FAIL**,
  reproduced during COPY into `legal_material_preparations`, because the archived
  checksum functions depend on the session search path. The failed QA restore
  rolled back and its database was removed. This was not archive-byte corruption.
- Independently reviewed, controlled historical recovery: **PASS**, actual
  pre-data, data and post-data execution, not merely `pg_restore --list`.
- Standard serial dump/restore of **new synthetic databases after migration
  `b9e5f3a7c012`**: **PASS** in disposable PostgreSQL tests. The production schema
  was not migrated by this audit; old archive bytes were not rewritten.

## Isolation and execution

SSH used the pinned host identity and deployment-admin key, BatchMode and strict
host-key checking. The production PostgreSQL container identity was verified from
its exact Compose labels. A unique `TEMPLATE template0` QA database had connection
limit two and revoked PUBLIC access: zero non-superuser login roles could CONNECT.
No application was attached to it. Cleanup checked the exact generated database
name, OID and private marker before DROP; it did not terminate other connections.

Initial host headroom was 2,481 MiB available memory and 4,847,000 KiB available
disk; PostgreSQL used 142.4 MiB of its 384 MiB limit. Restore sessions used
`work_mem=4MB`, `maintenance_work_mem=16MB`, no parallel query workers, a 300-second
statement timeout and a five-second lock timeout.

| Step | Actual result | Elapsed |
| --- | --- | --- |
| Restore pre-data only | `pg_restore` exit 0 | 296 ms |
| Execute only the two reviewed `HASH_FUNCTIONS_SQL` definitions | transaction committed | 122 ms |
| Restore data only | `pg_restore` exit 0; archive producer exit 0 | 4,190 ms |
| Restore post-data only | `pg_restore` exit 0 | 370 ms |
| Entire controlled recovery, including metadata checks between phases | PASS | 5,249 ms |

Every restore phase used `--single-transaction --exit-on-error --no-owner
--no-privileges --no-tablespaces`. The pre/post-data section consumers exited
successfully before their archive producer finished, producing expected producer
SIGPIPE 141; the complete data phase consumed the archive successfully. The
scoped repair preserved function OIDs, owner, ACLs, configuration, strictness,
immutability and parallel-safety flags. It did not advance Alembic or remove any
CHECK. The SQL definitions were inserted verbatim from reviewed commit `6555f5f`.

An earlier controlled attempt stopped before data because operator-side SQL
string substitution collapsed dollar quotes. Its exact QA database was cleaned
up, archive/schema fingerprints remained unchanged, and the retry required
verbatim SQL equality before execution. No partial attempt is counted as PASS.

## Restored integrity and inventory

Verified: 47 public tables, two public views, 78 foreign keys, 27 row-security
tables, 27 policies, four extensions, and the original Alembic revision. Counts
for unvalidated constraints, invalid indexes and disabled non-internal triggers
were all zero. Receipt artifact/size, preparation canonical metadata, and non-null
normative artifact/text hash mismatch counts were all zero. Checks computed hashes
inside PostgreSQL; no raw documents or case content were returned.

All 47 table counts were collected. Nonzero counts were:

```text
alembic_version=1
audit_events=14
clinic_users=3
clinics=1
idempotency_records=8
legal_documents=7
legal_fragments=63
legal_material_preparations=58
legal_review_materials=58
legal_sources=5
legal_versions=10
reference_evaluation_access_grants=3
risk_policy_events=1
risk_policy_versions=1
subscription_entitlement_events=3
subscription_entitlements=3
telegram_intake_drafts=4
users=3
```

The remaining 29 tables had zero rows:

```text
analysis_jobs, case_analysis_claims, case_analysis_runs,
case_escalation_messages, case_escalation_workflow_events, case_escalations,
case_facts, case_materials, case_reports, case_retention_events,
case_risk_assessments, cases,
clinic_document_approval_events, clinic_document_fragments,
clinic_document_versions, clinic_documents, escalation_notifications,
legal_approval_events, legal_fragment_embeddings, legal_prepared_part_versions,
legal_reference_review_events, legal_update_review_items, legal_update_runs,
legal_watch_discoveries, legal_watch_review_events,
reference_evaluation_case_versions, reference_evaluation_cases,
reference_evaluation_review_events, telegram_case_workflows
```

This contains 58 original receipts and 58 preparation cards, not 58 legally
approved normative versions. Normative and reference approval-event counts are
zero; no lawyer's review was performed by the audit.

## Cleanup, retained evidence and release checks

The successful QA database was dropped after verification. Exact database and QA
prefix remaining counts were zero. Production schema fingerprint (function
definitions/OIDs, constraints, table security/owners and Alembic revision) remained
unchanged; production received only read-only catalog queries. Archive SHA remained
unchanged. PostgreSQL used 175.6 MiB of its 384 MiB limit after recovery.

Private operational evidence is retained on the VPS in
`/var/lib/dental-legal-ai/backup-restore-qa-20261007.K12hJK/`. Directory/file ownership
and permissions are root `0700`/`0600`; restore, repair and verification stderr
files are empty. These operational logs are not committed or copied to a provider.

After rebasing the fix onto main `8ff81a4`, combined local gates passed: 1,308
tests with 165 explicitly enabled integration skips; fresh full Core PostgreSQL
suite 760 passed with one separately enabled MinIO smoke skip; Ruff, mypy across
all three services (125 modules), Compose and the worktree Code Graph passed.
New synthetic standard-restore tests include preservation of existing function
metadata and downgrade compatibility. Reintroducing an unqualified `digest`
caused the actual restore regression test to fail. CI runs that test using its
PostgreSQL service container, rather than silently omitting the restore step.

Remaining requirements: GitHub CI/release of the fix; a subsequent production
backup after migration; offsite replication, global-role/secrets recovery,
MinIO/object-storage recovery, and full application disaster recovery. None of
those is implied by this single-cluster PostgreSQL recovery proof.
