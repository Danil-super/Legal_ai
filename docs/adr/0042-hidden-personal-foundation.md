# ADR 0042: Two bot entrypoints with an inert personal foundation

Date: 2026-09-24
Status: Accepted for M1 engineering only; personal public launch NOT approved.

## Decision and user constraint

Keep the current clinic bot and UI unchanged. Build a second personal bot with
patient and employee scenarios. A personal scenario is not a clinic membership or
a role in clinic_users. The same human can have independent personal and work
contexts. This extends the clinic-only product specification, not the privileges
of the existing application or its subscriptions.

## M1 implementation

New code lives in legal_core.personal and the standalone
telegram_gateway.personal_preview entrypoint. Neither is imported into clinical
routing/composition. No production Compose/env/workflow activation is added.

PERSONAL_PREVIEW_MODE defaults to off. Only the explicit value synthetic permits
an allowlisted preview of six fixed, fictional scenarios. Other values fail
closed. The standalone HTTP factory registers only health while off; in synthetic
mode it provides two read-only endpoints behind an independent service secret AND
an explicit tester list. It does not use X-Telegram-User-Id or clinic roles as
authority. No write, case, document upload, identity creation, payment or LLM
endpoint exists. Documentation/OpenAPI are disabled. Responses are no-store.

The second bot validates an independent Telegram bot ID (including rotated-token
reuse), then restricts navigation to explicitly allowlisted private users. It
rejects free text and files without reading, downloading, persisting or echoing
their contents. This cannot stop a person from sending data to Telegram itself;
there is no production invitation or public onboarding in M1. Do not distribute
the preview as a confidential medical cabinet.

Personal contracts define workspace/owner scope, patient/employee topics and a
per-event timeline with exact/approximate/unknown dates and explicit confirmation.
A pure owner-access policy denies clinical authority even for the same human.
These are policy/schema tests, NOT database/RLS isolation: there are no personal
tables or authority resolver yet. No fake clinic is created for individuals.
Existing clinic-owned records still require clinic_id as specified by AGENTS.md.

The launch-readiness checklist is explanatory data, not an activation API. Live
personal analysis remains unconditionally unavailable, even if every checklist
item is set. Later code/review is required, not a hidden environment bypass.

## Data and legal boundaries

No new categories of personal data are collected. Real intake/storage/auth,
consents and transfer arrangements, separate credentials/search/queues, clinician
and patient representatives, retention and lawyer conflict checks are M2+ gates.
No provider or trusted-source policy changes. New legal source register lives in
docs/personal, is explicitly NOT a production ingestion manifest, and every entry
remains REVIEW_REQUIRED. Metadata discovery or an original ministry page does
not certify the current applicable edition. Existing approvals are not changed.

## Tests and release

Contract/authority/date checks, default-off and protected preview route tests,
real PTB dispatch with mocked Telegram, denial of foreign/shared chats, no file
fetch/echo, and clinical endpoint/composition baselines run in the existing CI.
Clinical UI/startup/Compose blob IDs are pinned while this user constraint stands;
updating that baseline requires a separate explicit interface/release review.

Use existing PR checks before main. A main deployment may contain inert code but
does not start the personal bot. No new service credentials are needed for normal
clinic deployment. No SQL migration or data rollback is needed for M1.
