# Internal work-product version storage

Owner-approved ADR0100 / Checkpoint A, 27 October 2026. This additive foundation
creates `case_work_product_versions`, not an API, queue, producer or release toggle.

Each version retains its immutable draft-shaped payload and canonical SHA256,
clinic/case/product/version/immediate-parent identity, true author membership,
source operation and fact/context/date/policy bindings. A composite key is shared
by all authors, so changing actor cannot duplicate version 1. Parent FKs cannot
cross a case, product or clinic. Payload identity/context fields must match columns;
missing JSON fields cannot pass a CHECK through SQL NULL. Content is capped at 64KiB.
The existing pure canonical JSON/SHA helpers establish integrity, not legal truth.

SQL metadata permits AI/UNAPPROVED and later HUMAN_REVISED/HUMAN_APPROVED versions
with coherent reviewer/time fields. The model payload always remains UNAPPROVED;
human review metadata is separate and cannot approve a normative document, create
a canonical report or send a patient response. No human approval operation is
implemented. Future Core writers must authorize the actual active/assigned lawyer,
recheck current facts/evidence, reject stale leases/completions and preserve an
already human-reviewed version. The table alone does not provide those guarantees.

RLS is forced by clinic. Runtime UPDATE/DELETE are revoked even after provisioning,
and an immutable trigger also protects against accidental privileged mutation.
A BEFORE INSERT guard rechecks Case availability after any row-lock wait and
rejects expired/purged content, preventing a late insert from escaping retention.
It cannot reorder the table lock that INSERT already acquired: application writers
must lock Case BEFORE issuing INSERT. Direct SQL insertion without that ordering
can deadlock with retention's trigger DDL (40P01); the actual AIF08 writer must have
a concurrency regression before enablement. Application authorization and current
source/evidence verification remain mandatory.

The existing retention function is extended from its installed definition with
checked anchors, retaining its material/job/workflow cleanup. It removes all work
product payload versions with case content; existing metadata-only audit can remain
under its current TTL. There is no new external store, PII category or retention term.

Migration `f31c8d5a9e20` adds the table to `e21b7c4d8a90`. Downgrade locks the table
before checking emptiness and refuses to erase retained history. An empty-table
downgrade restores the previous retention function exactly. Old application readers
remain compatible throughout; no producer is activated by this migration.
The guard temporarily removes FORCE RLS under its exclusive owner lock so even a
non-BYPASSRLS migration owner sees retained rows. On refusal PostgreSQL/Alembic's
transaction restores FORCE RLS; on success the proven empty table is removed.
The migration test's history probe also refuses RLS-filtered reads. A real temporary
non-bypass table/function owner regression proves that hidden history cannot pass
as an empty table and that failed downgrade restores both data and forced RLS.

Tests use real PostgreSQL and a NOSUPERUSER/NOBYPASSRLS runtime role: tenant reads and
writes, composite FKs/parent/global version uniqueness, payload/hash/NULL binding,
review metadata, immutability after provisioning, actual retention, a two-connection
insert/retention-lock race, empty migration round-trip and nonempty rollback refusal.
These storage checks do not establish end-to-end AI quality or release readiness.
