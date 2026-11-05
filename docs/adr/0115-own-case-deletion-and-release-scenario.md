# ADR 0115: Delete own saved cases from history

Date: 2026-11-05. Status: accepted within the owner's requested case-deletion scope.

Each personal history row offers the case and a delete button. Deletion requires
explicit confirmation naming that case; cancel performs no mutation. The API checks
active subscribed membership, tenant and creator, including after waiting for the case
row lock. Being an owner does not grant deletion of another creator's case.

`DELETE /v1/case-history/{case_id}` returns an idempotent `DELETION_SCHEDULED` receipt.
It shortens the case retention deadline to the current time, marks it CANCELLED and
shortens attached material expiry in the same transaction. Existing content gates
immediately hide the case and reject report/material reads and further analysis.
The existing hourly storage-first purge removes raw objects before immutable content;
permitted metadata audit remains. Already delivered Telegram files cannot be recalled.
There is no new table, migration, global retention change, risk change or model change.

Tests must prove creator/tenant isolation, all supported creator roles, revocation,
idempotency, storage-first cleanup, inaccessible old PDF buttons, explicit confirmation,
cancel without mutation and page navigation after deleting the last row on a page.
Release checks additionally exercise a synthetic description, clarifications, actual
analysis, PDF delivery/history/deletion and the separate lawyer review flow. Software
verification does not substitute for a lawyer's acceptance of legal conclusions.
