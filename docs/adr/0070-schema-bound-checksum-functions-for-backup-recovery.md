# ADR 0070: schema-bound checksum functions for backup recovery

Status: independently reviewed candidate; deployment still requires CI.

A real pre-import pg_dump failed standard single-transaction restore while loading
`legal_material_preparations`: its checksum CHECK calls
`legal_regression_result_sha256`, which called `legal_canonical_jsonb` without a
schema. The recursive canonical helper and pgcrypto `digest` were also unqualified.
`pg_restore` deliberately empties `search_path`; normal application tests did not
exercise that environment. This is an executable-schema recovery defect, not proof
that the historical archive's compressed data is damaged.

Migration `b9e5f3a7c012` replaces only those two existing function definitions.
Recursive/custom references and pgcrypto digest are bound to `public`; built-in
functions/collation are bound to `pg_catalog`. Canonical ordering, encoding, hashes,
strict/immutable/parallel-safe behavior, signatures, ownership, existing ACLs,
CHECK constraints and stored records are unchanged. A broad function/session search
path override or removing/recomputing the checksum CHECK was rejected. Downgrade
retains the schema-compatible qualified definitions instead of reintroducing the
recovery defect; it does not rewrite any historic migration.

Synthetic PostgreSQL tests reproduce empty-search-path failure before the fix,
compare nested canonical JSON/hash with the existing Python contract, and execute
real serial pg_dump/pg_restore against uniquely created disposable databases. CI
supplies its PostgreSQL service container ID; neither fixture may target runtime
databases or print archive/error-row contents.

New archives taken after migration must pass default single-transaction restore.
The historical archive remains immutable and still contains old definitions. Its
separate controlled recovery may restore pre-data into a unique, application-denied
QA database, execute only the reviewed `HASH_FUNCTIONS_SQL` definitions there, then
restore data and post-data. This is not a production repair, schema upgrade, legal
approval or successful default restore of the old archive. Review that procedure,
check all table/schema aggregates and the unchanged archive SHA, then delete only
the exactly created QA database. Offsite replication and MinIO recovery are separate
unverified requirements.

The 2026-10-07 controlled historical recovery passed in an application-denied QA
database; its schema, all table counts and artifact/checksum aggregates were
verified before exact-target cleanup. This does not change the old archive's
default-restore failure or authorize a manual production migration. See
[the verification record](../operations/backup-recovery-verification-2026-10-07.md).

Reference: [PostgreSQL 16 pg_restore](https://www.postgresql.org/docs/16/app-pgrestore.html).
