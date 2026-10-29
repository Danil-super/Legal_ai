# ADR 0108: Owner-approved quota for one clinic

29 October 2026. The owner explicitly approved increasing only their clinic's
monthly confirmed-case quota from 30 to 100 after the live attachment test reached
the existing quota. Other clinics and all legal/access checks remain unchanged.

Core accepts one optional trusted operator configuration pair:
`CASE_MONTHLY_LIMIT_OVERRIDE_CLINIC_ID` and
`CASE_MONTHLY_LIMIT_OVERRIDE_VALUE`. Both empty retains the existing 30-case
default. The configured identifier must be a canonical nonzero UUID; the value
must be a canonical bounded integer from 1 to 1000. A partial or malformed pair
fails closed with a sanitized 503 response, never an unlimited quota. Configure
only the approved clinic and value 100 during activation.

The effective limit is selected using the clinic identifier already resolved
server-side from the authenticated actor. Request headers/payloads cannot select
the override clinic. The existing clinic-wide advisory transaction lock, UTC
calendar-month counter, active-case limit, subscription/role checks, idempotent
workflow replay and audit trail remain intact. Owners and administrators in that
same clinic share its approved quota; another clinic retains 30.

There is no new table, migration, permission, service, risk policy, model setting,
or legal-source approval. Disabling both configuration values restores 30 without
deleting cases, receipts or reports. Changing operator configuration requires the
same explicit authorization and environment backup as other quota changes.

Tests cover default and foreign-clinic isolation, invalid/partial configuration,
real confirmations 31 and 101 for both owner/admin roles, retained replay at the
quota, and rejection of an inactive subscription. Only synthetic identities and
case metadata are used in disposable PostgreSQL.
