# Synthetic Clinic Documents fixtures

## Why this pack exists

`services/legal_core/fixtures/clinic_documents/` is a development/regression pack for the
Clinic Documents subsystem. The document taxonomy was informed by publicly visible Russian dental
clinic document libraries, but **all fixture text is original synthetic text** written for this
repository.

The pack is deliberately labelled:

- `DEVELOPMENT_AND_REGRESSION_ONLY`;
- `NOT_A_LEGAL_SOURCE`;
- `DO_NOT_USE_AS_REAL_CLINIC_POLICY`.

It must never be presented to a real clinic as an approved contract, informed-consent form,
warranty policy, patient memo or legal template.

## Contents

The current v1 pack contains eight documents for the fictional clinic `Тест-Дент`:

1. paid dental services contract;
2. warranty/service-life policy;
3. general informed consent;
4. implant informed consent;
5. patient rules;
6. medical-record access/request procedure;
7. post-implant memo;
8. internal claim-handling policy.

The manifest also contains four retrieval scenarios: implant complaint, crown/refund complaint,
medical-record request and formal claim/refund.

## What CI proves

The test suite verifies that:

- the files do not contain known public clinic names from the reference registry;
- they contain no URLs, Telegram handles or Russian-style phone numbers;
- every document passes the same normalisation/chunking code used by Clinic Documents;
- the manifest is bounded and deterministic;
- tenant retrieval scenarios are exercised against PostgreSQL;
- the pack is created, versioned and approved through the real Clinic Documents REST endpoints;
- approved documents can be retrieved only through the tenant-scoped approved context.

## Development loader

A guarded loader can seed the entire pack into an existing local development tenant through Legal
Core APIs. It never writes directly to PostgreSQL.

Dry run:

```bash
python -m legal_core.synthetic_clinic_loader \
  --telegram-user-id <LOCAL_TEST_ADMIN_ID> \
  --base-url http://127.0.0.1:8000
```

Apply:

```bash
export ALLOW_SYNTHETIC_CLINIC_FIXTURES=1
python -m legal_core.synthetic_clinic_loader \
  --telegram-user-id <LOCAL_TEST_ADMIN_ID> \
  --base-url http://127.0.0.1:8000 \
  --apply
```

The loader accepts only loopback targets or the internal Compose hostname `legal-core`. Both
`--apply` and `ALLOW_SYNTHETIC_CLINIC_FIXTURES=1` are required for writes. Re-running the command is
idempotent because Legal Core reuses content-identical versions and matching approval events.

## Conflict hints

Before approved clinic-document text crosses into Hermes, the agent projection performs a narrow,
deterministic scan for risky absolute wording such as an unconditional `no refund` statement or a
blanket waiver of all claims.

The result is only a stable `conflictHints` reason code. It **does not** declare the clause invalid
or unlawful. It instructs the researcher to compare the internal wording against approved mandatory
law evidence and avoid repeating the internal rule to a patient as if it were law.

Clinic-document fragment IDs are still removed before the Hermes boundary, and the independent
legal reviewer still receives only approved legal evidence, never clinic documents.

## Production boundary

For a real tenant:

- ingest only that clinic's own documents under its onboarding/data agreement;
- preserve tenant isolation and immutable version history;
- require explicit clinic approval before retrieval;
- use clinic documents only as internal/contractual context;
- use approved Legal Core evidence for legal conclusions;
- if an internal document conflicts with mandatory law, mandatory law wins and the document must be
  escalated for human review.
