# Specification: administrator intake, unified report and first legal corpus

## Status

Approved for incremental implementation on 2026-08-22. This specification refines
Dental Legal AI TZ v0.1 without enabling production use with real patient data.

## User and safety boundary

- The Telegram user is a clinic administrator and a subscriber to the service, not a patient
  and not a legal editor.
- Legal Core resolves `clinic_id` and actor identity from a server-owned Telegram mapping.
- Access is granted to one Telegram user for one clinic by an active, time-bounded subscription
  entitlement. A clinic membership alone does not grant service access. Subscription state is
  enforced by Legal Core for every protected Case Core and legal-retrieval request.
- The initial implementation deliberately contains no payment credentials, card data or payment
  provider integration. After a verified purchase, the configured platform owner may use
  `/grant_access <Telegram_ID>` in the bot. Legal Core verifies the owner ID server-side, creates
  an isolated clinic membership for a previously unknown ID, and grants the `MVP_MANUAL`
  entitlement. If the target already has exactly one active administrator membership, that
  entitlement is updated; several memberships are rejected rather than choosing a tenant.
  Selecting an acquirer, webhook contract, refund policy or self-service billing is a separate
  approved capability.
- `LEGAL_EDITOR` is an internal platform role for the legal-corpus release process. A subscriber
  can never obtain it through clinic onboarding or a paid plan.
- The intake asks for an internal pseudonymous patient reference only; names, phone numbers,
  diagnoses and uploaded documents are outside this slice.
- Raw Telegram messages and fact values are never written to application logs or audit metadata.
- Nothing is sent to a patient automatically. Any eventual response remains a marked draft that
  requires human approval.

## Administrator workflow

The `Создать кейс` conversation collects one atomic answer per step:

1. incident types and a primary incident type;
2. service type, service date and incident date;
3. a neutral, observable problem summary;
4. the patient's demand and, when applicable, the amount in integer kopecks;
5. claim date, written-claim status and applicable response deadline;
6. harm, hospitalisation, lawyer, regulator and court signals as `YES/NO/UNKNOWN`;
7. an inventory of available, missing, requested and non-applicable documents;
8. review, finalise or cancel.

Dates have `EXACT`, `APPROXIMATE` or `UNKNOWN` precision. Unknown critical facts remain visible;
they are not silently converted to false values. Corrections create a new fact that supersedes
the previous fact instead of deleting history.

## Case state machine

```text
COLLECTING -> NEEDS_INFORMATION -> INTAKE_COMPLETE
                                      |
                                      +-> ANALYSIS_BLOCKED
                                      +-> READY_FOR_ANALYSIS

READY_FOR_ANALYSIS -> ANALYZING -> REPORT_READY | ESCALATION_REQUIRED
```

Finalisation fails with `INSUFFICIENT_FACTS` while a critical fact is missing. Until the legal
corpus, effective-date resolver, approved-only retrieval and verifier gates are ready, a
finalised intake is `ANALYSIS_BLOCKED`.

## REST contract

All state-changing requests require `Idempotency-Key`. Reusing a key with a different request
body fails deterministically. `clinic_id` and `created_by` are never accepted in request bodies.

```text
POST /v1/cases
GET  /v1/cases/{case_id}
GET  /v1/cases/{case_id}/intake
POST /v1/cases/{case_id}/facts
POST /v1/cases/{case_id}/intake-finalizations
GET  /v1/cases/{case_id}/reports
POST /v1/cases/{case_id}/reports
GET  /v1/reports/{report_id}
GET  /v1/reports/{report_id}/pdf
GET  /v1/legal/fragments?query=...&as_of_date=...
POST /v1/platform/subscription-grants
```

The gateway sends only the Telegram user identity. Legal Core obtains tenant and role from the
database. Unknown and non-admin users receive `403` without revealing whether a case exists.
Mapped administrators without an active entitlement receive `403` with the stable
`SUBSCRIPTION_INACTIVE` code; the gateway displays only a neutral subscription-support message.
`POST /v1/platform/subscription-grants` additionally requires an `Idempotency-Key`, an active
owner entitlement and a Telegram ID equal to the server-side `PLATFORM_OWNER_TELEGRAM_ID`; it is
not a public self-service endpoint.

Errors use one envelope:

```json
{
  "error": {
    "code": "INSUFFICIENT_FACTS",
    "message": "Карточку пока нельзя подтвердить",
    "details": {"missingFactKeys": ["CLAIM_DATE"]},
    "correlationId": "uuid"
  }
}
```

## Persistence contract

The first migration creates:

- `clinics`, `users`, `clinic_users` for server-owned identity and tenant membership;
- `subscription_entitlements` and append-only `subscription_entitlement_events` for an
  individual user's paid service access within one clinic;
- `cases`, `case_facts`, `case_reports`, `audit_events` for immutable case history;
- `legal_sources`, `legal_documents`, `legal_versions`, `legal_fragments` for versioned law;
- `idempotency_records` for atomic replay protection.

Every tenant-owned row contains `clinic_id`. Case facts are append-only. Reports are immutable;
changing a fact supersedes earlier reports and a new report version is created. PDF bytes and
their SHA-256 are stored with the tenant-scoped report in this local MVP; object storage is the
planned production storage boundary.

## Canonical report

`dental-case-report.v1` is the only rendering source for Telegram and PDF. It always contains:

1. case number, version, status and generated timestamp;
2. neutral summary and incident types;
3. timeline and facts grouped by provenance;
4. patient demands and document inventory;
5. missing facts and urgent manual-review signals;
6. risk with reason codes and policy version, or an explicit unavailable state;
7. current actions and prohibited actions, or an explicit unavailable state;
8. a visibly marked draft response, or an explicit unavailable state;
9. legal sources with document/article/version/effective dates/official URL/SHA-256;
10. confidence, escalation and traceability hashes;
11. the disclaimer that this is an internal draft and not a final legal opinion.

Before evidence gates pass, the report remains useful as an intake summary but uses:

```json
{
  "analysisAvailability": {
    "status": "BLOCKED",
    "reasonCode": "LEGAL_CORPUS_NOT_READY"
  },
  "recommendations": {"status": "NOT_AVAILABLE", "items": []},
  "draftResponse": {"status": "NOT_AVAILABLE", "text": null},
  "legalBasis": {"status": "NOT_AVAILABLE", "sources": []}
}
```

## Legal corpus gate

The initial verified corpus is ingested only from official publication or official government
sources. Each immutable version records source URL, retrieval timestamp, MIME type, raw SHA-256,
version date, `effective_from`, open-ended `effective_to`, review status and structural
fragments. Production retrieval returns only `APPROVED` versions applicable on
`as_of_date`.

The transition between Government Decree No. 736 and No. 659 is a mandatory time-travel test:
No. 736 is applicable before 2026-09-01; No. 659 is applicable from 2026-09-01. Publishing a
document does not make it applicable before its effective date.

No recommendation, risk conclusion, legal claim or patient-response draft may be enabled merely
because a document was downloaded. Approval, date resolution, fragment retrieval and claim-to-
evidence verification must all pass first.

## Acceptance criteria

- An authenticated `CLINIC_ADMIN` can create and resume a synthetic case through Telegram.
- Unknown users and cross-tenant reads/writes/reports/PDF access are denied by tests.
- Duplicate create/fact/report requests do not create duplicate records.
- Missing critical facts block finalisation and return the next required question.
- The case, facts and report tables are produced by an Alembic migration and downgrade cleanly.
- Telegram summary and PDF share report ID, version and fact snapshot SHA-256.
- The initial legal corpus has reproducible raw SHA-256 values and official source metadata.
- Retrieval excludes `REVIEW_REQUIRED`, future and expired versions.
- Recommendation and draft fields remain unavailable until every evidence gate passes.
- A mapped administrator with no active entitlement, a suspended entitlement or an expired
  entitlement cannot call any protected Legal Core endpoint or begin a Telegram case.
- A clinic administrator can never be made a `LEGAL_EDITOR` by subscription provisioning.
- Only the configured platform owner can grant access by Telegram ID; the API denies every other
  mapped subscriber and idempotent retries do not create an additional clinic or entitlement.
